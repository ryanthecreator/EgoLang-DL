# Copyright (c) 2023 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
#
# Licensed under the NVIDIA Source Code License [see LICENSE for details].
"""
The main entry point for training policies. Adapted to use PyTorch Lightning and Optimus codebase.

Args:
    config (str): path to a config json that will be used to override the default settings.
        If omitted, default settings are used. This is the preferred way to run experiments.

    algo (str): name of the algorithm to run. Only needs to be provided if @config is not
        provided.

    name (str): if provided, override the experiment name defined in the config

    dataset (str): if provided, override the dataset path defined in the config

    debug (bool): set this flag to run a quick training run for debugging purposes
"""
import argparse
import json
import os
import traceback
from mimicplay.configs import config_factory
import datetime
import time
from mimicplay.scripts.aloha_process.simarUtils import nds
import matplotlib.pyplot as plt
from mimicplay.pl_utils.pl_train_utils import train, eval
from mimicplay.pl_utils.pl_data_utils import json_to_config
import torch

def main(args):
    if args.config is not None:
        ext_cfg = json.load(open(args.config, "r"))
        config = config_factory(ext_cfg["algo_name"])
        # update config with external json - this will throw errors if
        # the external config has keys not present in the base algo config
        with config.values_unlocked():
            config.update(ext_cfg)
    elif args.ckpt_path is not None:
        ckpt = torch.load(args.ckpt_path, map_location="cpu")
        config = json_to_config(ckpt["hyper_parameters"]["config_json"])
    else:
        assert False, "Must provide a config file or a ckpt path"


    if args.dataset is not None:
        config.train.data = args.dataset
    
    if args.dataset_2 is not None:
        config.train.data_2 = args.dataset_2

    if args.output_dir is not None:
        config.train.output_dir = args.output_dir

    if args.name is not None:
        config.experiment.name = args.name

    if args.seed is not None:
        config.train.seed = args.seed
    
    if args.description is not None:
        config.experiment.description = args.description
    
    if args.lr:
        config.algo.optim_params.policy.learning_rate.initial = args.lr

    if args.batch_size:
        config.train.batch_size = args.batch_size

    config.train.gpus_per_node = args.gpus_per_node
    config.train.num_nodes = args.num_nodes
    # maybe modify config for debugging purposes
    if args.debug:
        # shrink length of training to test whether this run is likely to crash
        config.unlock()
        config.lock_keys()

        # train and validate (if enabled) for 1 gradient steps, for 2 epochs
        # config.train.fast_dev_run = 2
        config.train.num_epochs = 10
        config.experiment.save.every_n_epochs = 5


        # if rollouts are enabled, try 10 rollouts at end of each epoch, with 10 environment steps
        config.experiment.epoch_every_n_steps = 10

        # send output to a temporary directory
        config.experiment.logging.log_wandb=False
        config.experiment.logging.wandb_proj_name=None

        config.experiment.validation_max_samples = 64
        config.experiment.validation_freq = 2
        config.experiment.save.every_n_epochs = 2
        config.experiment.save.video_freq = 2
        config.experiment.name = "debug_run"
    elif args.profiler != "none":
        # shrink length of training to test whether this run is likely to crash
        config.unlock()
        config.lock_keys()

        config.experiment.epoch_every_n_steps = 10
        config.train.num_epochs = 1
        config.train.num_data_workers = 0

        # if rollouts are enabled, try 2 rollouts at end of each epoch, with 10 environment steps
        # config.experiment.rollout.rate = 1
        # config.experiment.rollout.n = 1

        # send output to a temporary directory
        config.experiment.logging.log_wandb=False
        config.experiment.logging.wandb_proj_name=None
    else:
        config.wandb_project_name = args.wandb_project_name
        config.train.fast_dev_run = False

    if config.train.gpus_per_node == 1 and args.num_nodes == 1:
        os.environ["OMP_NUM_THREADS"] = "1"
    
    if args.no_wandb:
        config.experiment.logging.log_wandb=False
        config.experiment.logging.wandb_proj_name=None
    
    assert config.experiment.validation_freq % config.experiment.save.every_n_epochs == 0, "current code expects validation_freq to be a multiple of save.every_n_epochs"
    assert config.experiment.validation_freq == config.experiment.save.video_freq, "current code expects validation_freq to be the same as save.video_freq"

    # lock config to prevent further modifications and ensure missing keys raise errors
    config.lock()

    # catch error during training and print it
    res_str = "finished run successfully!"
    important_stats = None
    try:
        if args.eval:
            eval(config, args.ckpt_path)
            return
        else:
            important_stats = train(config, args.ckpt_path)
        important_stats = json.dumps(important_stats, indent=4)
    except Exception as e:
        res_str = "run failed with error:\n{}\n\n{}".format(e, traceback.format_exc())
    print(res_str)
    if important_stats is not None:
        print("\nRollout Success Rate Stats")
        print(important_stats)

def train_argparse():
    parser = argparse.ArgumentParser()

    # External config file that overwrites default config
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="(optional) path to a config json that will be used to override the default settings. \
            If omitted, default settings are used. This is the preferred way to run experiments.",
    )

    # Algorithm Name
    parser.add_argument(
        "--algo",
        type=str,
        help="(optional) name of algorithm to run. Only needs to be provided if --config is not provided",
    )

    # Experiment Name (for tensorboard, saving models, etc.)
    parser.add_argument(
        "--name",
        type=str,
        default=None,
        help="(optional) if provided, override the experiment name defined in the config",
    )

    # description
    parser.add_argument(
        "--description",
        type=str,
        default=None,
        help="description",
    )

    # Dataset path, to override the one in the config
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="(optional) if provided, override the dataset path defined in the config",
    )

    # Dataset path, to override the one in the config
    parser.add_argument(
        "--dataset_2",
        type=str,
        default=None,
        help="(optional) if provided, override the dataset path defined in the config",
    )

    # Output path, to override the one in the config
    parser.add_argument(
        "--output_dir",
        type=str,
        default=None,
        help="(optional) if provided, override the output path defined in the config",
    )

    # debug mode
    parser.add_argument(
        "--debug",
        action="store_true",
        help="set this flag to run a quick training run for debugging purposes",
    )

    # env seed
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="(optional) if provided, sets the seed",
    )

    parser.add_argument(
        "--lr",
        type=float,
        default=None,
        help="learning rate"
    )

    parser.add_argument(
        "--batch-size",
        type=int,
        default=None,
        help="batch size"
    )

    parser.add_argument(
        "--wandb_project_name",
        type=str,
        default="egoplay",
    )

    parser.add_argument(
        "--ckpt_path",
        type=str,
        default=None,
        help="path to pytorch lightning ckpt file",
    )

    parser.add_argument(
        "--eval",
        action="store_true",
        help="set this flag to run a evaluation"
    )

    parser.add_argument(
        "--resume_dir",
        type=str,
        default=None,
        help="path to pytorch lightning resume dir",
    )

    parser.add_argument(
        "--profiler",
        type=str,
        default="none",
        help="profiler to use (none, pytorch, simple, advanced)",
    )

    parser.add_argument(
        "--gpus-per-node",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--num-nodes",
        type=int,
        default=1,
    )

    parser.add_argument(
        "--no-wandb",
        action='store_true',
        help="set this flag to run a without wandb"
    )

    parser.add_argument(
        "--overcap",
        action='store_true',
        help="overcap partition"
    )

    args = parser.parse_args()

    return args


if __name__ == "__main__":
    args = train_argparse()

    if not args.eval and "DT" not in args.description:
        time_str = f"{args.description}_DT_{datetime.datetime.fromtimestamp(time.time()).strftime('%Y-%m-%d-%H-%M-%S')}"
        args.description = time_str

    main(args)