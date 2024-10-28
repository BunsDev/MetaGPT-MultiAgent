# SELA: Tree-Search Enhanced LLM Agents for Automated Machine Learning



## 1. Data Preparation

- Download Datasets：https://deepwisdom.feishu.cn/drive/folder/RVyofv9cvlvtxKdddt2cyn3BnTc?from=from_copylink
- Download and prepare datasets from scratch:
```
cd data
python dataset.py --save_analysis_pool
python hf_data.py --save_analysis_pool
```

## 2. Configs

### Data Config

`datasets.yaml` Provide base prompts, metrics, target columns for respective datasets

- Modify `datasets_dir` to the root directory of all the datasets in `data.yaml`


### LLM Config

```
llm:
  api_type: 'openai'
  model: deepseek-coder
  base_url: "https://your_base_url"
  api_key: sk-xxx
  temperature: 0.5
```

### Budget
Experiment rollouts k = 5, 10, 20


### Prompt Usage

- Use the function `generate_task_requirement` in `dataset.py` to get task requirement.
  - If the method is non-DI-based, set `is_di=False`.
  - Use `utils.DATA_CONFIG` as `data_config`


## 3. SELA

### Run SELA

#### Setup
In the root directory, 

```
pip install -e .

cd expo

pip install -r requirements.txt
```

#### Run

- Examples
  ```
  python run_experiment.py --exp_mode mcts --task titanic --rollouts 10
  python run_experiment.py --exp_mode mcts --task house-prices --rollouts 10 --low_is_better
  ```


- `--rollouts` - The number of rollouts

- `--use_fixed_insights` - In addition to the generated insights, include the fixed insights saved in `expo/insights/fixed_insights.json`
  
- `--low_is_better` - If the dataset has reg metric, remember to use `--low_is_better`

- `--from_scratch` - Do not use pre-processed insight pool, generate new insight pool based on dataset before running MCTS, facilitating subsequent tuning to propose search space prompts

- `--role_timeout` - The timeout for the role
  - This feature limits the duration of a single simulation, making the experiment duration more controllable (for example, if you do ten rollouts and set role_timeout to 1,000, the experiment will stop at the latest after 10,000s)


- `--max_depth` - The maximum depth of MCTS, default is 4 (nodes at this depth directly return the previous simulation result without further expansion)

- `--load_tree` - If MCTS was interrupted due to certain reasons but had already run multiple rollouts, you can use `--load_tree`.
  - For example:
    ```
    python run_experiment.py --exp_mode mcts --task titanic --rollouts 10
    ```
  - If this was interrupted after running three rollouts, you can use `--load_tree`:
    ```
    python run_experiment.py --exp_mode mcts --task titanic --rollouts 7 --load_tree
    ```


#### Ablation Study

**DI RandomSearch**

- Single insight
`python run_experiment.py --exp_mode rs --task titanic --rs_mode single`

- Set insight
`python run_experiment.py --exp_mode rs --task titanic --rs_mode set`


## 4. Evaluation

Each baseline needs to produce `dev_predictions.csv`和`test_predictions.csv`. Each csv file only needs a `target` column.

- Use the function `evaluate_score` to evaluate.

#### MLE-Bench
**Note: mle-bench requires python 3.11 or higher**
```
git clone https://github.com/openai/mle-bench.git
cd mle-bench
pip install -e .
```

```
mlebench prepare -c <competition-id> --data-dir <dataset-dir-save-path>
```

Enter the following command to run the experiment:
```
python run_experiment.py --exp_mode mcts --custom_dataset_dir <dataset-dir-save-path/prepared/public> --rollouts 10 --from_scratch --role_timeout 3600
```


## 5. Baselines

### AIDE

#### Setup
The version of AIDE we use is dated September 30, 2024
```
git clone https://github.com/WecoAI/aideml.git
git checkout 77953247ea0a5dc1bd502dd10939dd6d7fdcc5cc
```

Modify `aideml/aide/utils/config.yaml` - change `k_fold_validation`, `code model`, and `feedback model` as follows:

```yaml
# agent hyperparams
agent:
  # how many improvement iterations to run
  steps: 10
  # whether to instruct the agent to use CV (set to 1 to disable)
  k_fold_validation: 1
  # LLM settings for coding
  code:
    model: deepseek-coder
    temp: 0.5

  # LLM settings for evaluating program output / tracebacks
  feedback:
    model: deepseek-coder
    temp: 0.5

  # hyperparameters for the tree search
  search:
    max_debug_depth: 3
    debug_prob: 0.5
    num_drafts: 5
```

Since Deepseek is compatible to OpenAI's API, change `base_url` into `your own url`，`api_key` into `your api key`

```
export OPENAI_API_KEY="your api key"
export OPENAI_BASE_URL="your own url"
```

Modify `aideml/aide/backend/__init__.py`'s line 30 and below:

```python
model_kwargs = model_kwargs | {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if "claude-" in model:
        query_func = backend_anthropic.query
    else:
        query_func = backend_openai.query
```

Since deepseekV2.5 no longer supports system message using function call, modify `aideml/aide/agent.py`'s line 312:

```python
response = cast(
            dict,
            query(
                system_message=None,
                user_message=prompt,
                func_spec=review_func_spec,
                model=self.acfg.feedback.model,
                temperature=self.acfg.feedback.temp,
            ),
        )
```

Modify and install:

```
cd aideml
pip install -e .
```

#### Run

Run the following script to get the running results, a `log` folder and a `workspace` folder will be generated in the current directory
The `log` folder will contain the experimental configuration and the generated scheme, and the `workspace` folder will save the final results generated by aide

```
python experimenter/aide.py
```

### Autogluon
#### Setup
```
pip install -U pip
pip install -U setuptools wheel
pip install autogluon==1.1.1
```

For Tabular data:
```
python run_expriment.py --exp_mode autogluon --task {task_name}
```
For Multimodal data:
```
python run_expriment.py --exp_mode autogluon --task {task_name} --is_multimodal
```
Replace {task_name} with the specific task you want to run.


### AutoSklearn
#### System requirements
auto-sklearn has the following system requirements:

- Linux operating system (for example Ubuntu)

- Python (>=3.7)

- C++ compiler (with C++11 supports)

In case you try to install Auto-sklearn on a system where no wheel files for the pyrfr package are provided (see here for available wheels) you also need:

- SWIG [(get SWIG here).](https://www.swig.org/survey.html)

For an explanation of missing Microsoft Windows and macOS support please check the Section [Windows/macOS compatibility](https://automl.github.io/auto-sklearn/master/installation.html#windows-macos-compatibility).

#### Setup
```
pip install auto-sklearn==0.15.0
```

#### Run
```
python run_experiment.py --exp_mode autosklearn --task titanic
```

### Base DI 
For setup, check 4.
- `python run_experiment.py --exp_mode base --task titanic --num_experiments 10`
- Specifically instruct DI to use AutoGluon: `--special_instruction ag`
- Specifically instruct DI to use the stacking ensemble method: `--special_instruction stacking`