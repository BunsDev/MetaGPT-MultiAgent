"""Experience Decorator."""

import asyncio
import functools
from typing import Any, Callable, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, model_validator

from metagpt.config2 import config
from metagpt.exp_pool.context_builders import BaseContextBuilder, SimpleContextBuilder
from metagpt.exp_pool.manager import ExperienceManager, exp_manager
from metagpt.exp_pool.perfect_judges import BasePerfectJudge, SimplePerfectJudge
from metagpt.exp_pool.schema import Experience, Metric, QueryType, Score
from metagpt.exp_pool.scorers import BaseScorer, SimpleScorer
from metagpt.logs import logger
from metagpt.utils.async_helper import NestAsyncio
from metagpt.utils.exceptions import handle_exception

ReturnType = TypeVar("ReturnType")


def exp_cache(
    _func: Optional[Callable[..., ReturnType]] = None,
    query_type: QueryType = QueryType.SEMANTIC,
    manager: Optional[ExperienceManager] = None,
    scorer: Optional[BaseScorer] = None,
    perfect_judge: Optional[BasePerfectJudge] = None,
    context_builder: Optional[BaseContextBuilder] = None,
    req_serialize: Optional[Callable[..., str]] = None,
    resp_serialize: Optional[Callable[..., str]] = None,
    resp_deserialize: Optional[Callable[[str], Any]] = None,
    tag: Optional[str] = None,
):
    """Decorator to get a perfect experience, otherwise, it executes the function, and create a new experience.

    1. This can be applied to both synchronous and asynchronous functions.
    2. The function must have a `req` parameter, and it must be provided as a keyword argument.
    3. If `config.exp_pool.enable_read` is False, the decorator will just directly execute the function.

    Args:
        _func: Just to make the decorator more flexible, for example, it can be used directly with @exp_cache by default, without the need for @exp_cache().
        query_type: The type of query to be used when fetching experiences.
        manager: How to fetch, evaluate and save experience, etc. Default to `exp_manager`.
        scorer: Evaluate experience. Default to `SimpleScorer()`.
        perfect_judge: Determines if an experience is perfect. Defaults to `SimplePerfectJudge()`.
        context_builder: Build the context from exps and the function parameters. Default to `SimpleContextBuilder()`.
        req_serialize: Serializes the request for storage. Defaults to `lambda req: str(req)`.
        resp_serialize: Serializes the function's return value for storage. Defaults to `lambda resp: str(resp)`.
        resp_deserialize: Deserializes the stored response back to the function's return value. Defaults to `lambda resp: resp`.
        tag: An optional tag for the experience. Default to `ClassName.method_name` or `function_name`.
    """

    def decorator(func: Callable[..., ReturnType]) -> Callable[..., ReturnType]:
        if not config.exp_pool.enable_read:
            return func

        @functools.wraps(func)
        async def get_or_create(args: Any, kwargs: Any) -> ReturnType:
            handler = ExpCacheHandler(
                func=func,
                args=args,
                kwargs=kwargs,
                query_type=query_type,
                exp_manager=manager,
                exp_scorer=scorer,
                exp_perfect_judge=perfect_judge,
                context_builder=context_builder,
                req_serialize=req_serialize,
                resp_serialize=resp_serialize,
                resp_deserialize=resp_deserialize,
                tag=tag,
            )

            await handler.fetch_experiences()
            if exp := await handler.get_one_perfect_exp():
                return exp

            await handler.execute_function()
            await handler.process_experience()

            return handler._raw_resp

        return ExpCacheHandler.choose_wrapper(func, get_or_create)

    return decorator(_func) if _func else decorator


class ExpCacheHandler(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    func: Callable
    args: Any
    kwargs: Any
    query_type: QueryType = QueryType.SEMANTIC
    exp_manager: Optional[ExperienceManager] = None
    exp_scorer: Optional[BaseScorer] = None
    exp_perfect_judge: Optional[BasePerfectJudge] = None
    context_builder: Optional[BaseContextBuilder] = None
    req_serialize: Optional[Callable[..., str]] = None
    resp_serialize: Optional[Callable[..., str]] = None
    resp_deserialize: Optional[Callable[[str], Any]] = None
    tag: Optional[str] = None

    _exps: list[Experience] = None
    _req: str = ""
    _resp: str = ""
    _raw_resp: Any = None
    _score: Score = None

    @model_validator(mode="after")
    def initialize(self):
        """Initialize default values for optional parameters if they are None.

        This is necessary because the decorator might pass None, which would override the default values set by Field.
        """

        self._validate_params()

        self.exp_manager = self.exp_manager or exp_manager
        self.exp_scorer = self.exp_scorer or SimpleScorer()
        self.exp_perfect_judge = self.exp_perfect_judge or SimplePerfectJudge()
        self.context_builder = self.context_builder or SimpleContextBuilder()
        self.req_serialize = self.req_serialize or (lambda resp: str(resp))
        self.resp_serialize = self.resp_serialize or (lambda resp: str(resp))
        self.resp_deserialize = self.resp_deserialize or (lambda resp: resp)
        self.tag = self.tag or self._generate_tag()

        self._req = self.req_serialize(self.kwargs["req"])

        return self

    async def fetch_experiences(self):
        """Fetch experiences by query_type."""

        self._exps = await self.exp_manager.query_exps(self._req, query_type=self.query_type, tag=self.tag)

    async def get_one_perfect_exp(self) -> Optional[Any]:
        """Get a potentially perfect experience, and resolve resp."""

        for exp in self._exps:
            if await self.exp_perfect_judge.is_perfect_exp(exp, self._req, *self.args, **self.kwargs):
                logger.info(f"Get one perfect experience: {exp.req[:20]}...")
                return self.resp_deserialize(exp.resp)

        return None

    async def execute_function(self):
        """Execute the function, and save resp."""

        self._raw_resp = await self._execute_function()
        self._resp = self.resp_serialize(self._raw_resp)

    @handle_exception
    async def process_experience(self):
        """Process experience.

        Evaluates and saves experience.
        Use `handle_exception` to ensure robustness, do not stop subsequent operations.
        """

        await self.evaluate_experience()
        self.save_experience()

    async def evaluate_experience(self):
        """Evaluate the experience, and save the score."""

        self._score = await self.exp_scorer.evaluate(self._req, self._resp)

    def save_experience(self):
        """Save the new experience."""

        exp = Experience(req=self._req, resp=self._resp, tag=self.tag, metric=Metric(score=self._score))
        self.exp_manager.create_exp(exp)

    @staticmethod
    def choose_wrapper(func, wrapped_func):
        """Choose how to run wrapped_func based on whether the function is asynchronous."""

        async def async_wrapper(*args, **kwargs):
            return await wrapped_func(args, kwargs)

        def sync_wrapper(*args, **kwargs):
            NestAsyncio.apply_once()
            return asyncio.get_event_loop().run_until_complete(wrapped_func(args, kwargs))

        return async_wrapper if asyncio.iscoroutinefunction(func) else sync_wrapper

    def _validate_params(self):
        if "req" not in self.kwargs:
            raise ValueError("`req` must be provided as a keyword argument.")

    def _generate_tag(self) -> str:
        """Generates a tag for the self.func.

        "ClassName.method_name" if the first argument is a class instance, otherwise just "function_name".
        """

        if self.args and hasattr(self.args[0], "__class__"):
            cls_name = type(self.args[0]).__name__
            return f"{cls_name}.{self.func.__name__}"

        return self.func.__name__

    async def _build_context(self) -> str:
        self.context_builder.exps = self._exps

        return await self.context_builder.build(*self.args, **self.kwargs)

    async def _execute_function(self):
        self.kwargs["req"] = await self._build_context()

        if asyncio.iscoroutinefunction(self.func):
            return await self.func(*self.args, **self.kwargs)

        return self.func(*self.args, **self.kwargs)
