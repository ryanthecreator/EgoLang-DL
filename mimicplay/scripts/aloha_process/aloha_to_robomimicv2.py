import h5py
import numpy as np
import argparse
import os
from tqdm import tqdm
from mimicplay.scripts.aloha_process.simarUtils import nds, ee_pose_to_cam_frame, EXTRINSICS
import pytorch_kinematics as pk
import torch
from modern_robotics import FKinSpace
from robomimic.scripts.split_train_val import split_train_val_from_hdf5
import json

# Example call: in aloha_process folder: python aloha_to_robomimic.py --dataset /coc/flash7/skareer6/calibrate_samples/ --arm left --out /coc/flash7/skareer6/calibrate_samples/ --task-name robomimic
"""
aloha_hdf5 has the following format
dict with keys:  <KeysViewHDF5 ['action', 'observations']>
action: (500, 14)
observations: dict with keys:  <KeysViewHDF5 ['effort', 'images', 'qpos', 'qvel']>
        effort: (500, 14)
        images: dict with keys:  <KeysViewHDF5 ['cam_high', 'cam_right_wrist']>
                cam_high: (500, 480, 640, 3)
                cam_right_wrist: (500, 480, 640, 3)
        qpos: (500, 14)
        qvel: (500, 14)
"""

def convert_qpos_to_eef(qpos):
    M = np.array([[1.0, 0.0, 0.0, 0.536494],
                  [0.0, 1.0, 0.0, 0.0],
                  [0.0, 0.0, 1.0, 0.42705],
                  [0.0, 0.0, 0.0, 1.0]])

    Slist = np.array([[0.0, 0.0, 1.0, 0.0, 0.0, 0.0],
                      [0.0, 1.0, 0.0, -0.12705, 0.0, 0.0],
                      [0.0, 1.0, 0.0, -0.42705, 0.0, 0.05955],
                      [1.0, 0.0, 0.0, 0.0, 0.42705, 0.0],
                      [0.0, 1.0, 0.0, -0.42705, 0.0, 0.35955],
                      [1.0, 0.0, 0.0, 0.0, 0.42705, 0.0]]).T

    T_obs = FKinSpace(M, Slist, qpos)
    return T_obs

def get_future_points(arr, POINT_GAP=15, FUTURE_POINTS_COUNT=10):
    future_traj = []

    for i in range(POINT_GAP, (FUTURE_POINTS_COUNT + 1) * POINT_GAP, POINT_GAP):
        # Identify the indices for the current and prior points
        index_current = min(len(arr) - 1, i)

        current_point = arr[index_current]
        future_traj.extend(current_point)

    return future_traj

def is_valid_path(path):
    return not os.path.isdir(path) and "episode" in path

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=str,
        help="path to rawAloha folder",
    )
    parser.add_argument(
        "--arm",
        type=str,
        help="which arm to convert data for"
    )
    parser.add_argument(
        "--extrinsics",
        type=str,
        help="which arm to convert data for"
    )
    parser.add_argument(
        "--out",
        type=str,
        help="path to output dataset: /coc/flash7/datasets/oboov2/<ds_name>.hdf5"
    )
    parser.add_argument(
        "--data-type",
        type=str,
        required=True,
        choices=['hand', 'robot'],  # Restrict to only 'hand' or 'robot'
        help="Choose which data-type - hand or robot"
    )
    args = parser.parse_args()

    chain = pk.build_serial_chain_from_urdf(open("/coc/flash9/skareer6/Projects/EgoPlay/EgoPlay/mimicplay/scripts/aloha_process/model.urdf").read(), "vx300s/ee_gripper_link")


    #before converting everything, check it all at least opens
    for file in tqdm(os.listdir(args.dataset)):
        # print("Trying to open " + file)
        if is_valid_path(os.path.join(args.dataset, file)):
            with h5py.File(os.path.join(args.dataset, file), "r") as f:
                pass

    with h5py.File(args.out, "w", rdcc_nbytes=1024**2*2) as dataset:
        data_group = dataset.create_group("data")
        data_group.attrs["env_args"] = json.dumps({}) # if no normalize obs

        for i, aloha_demo in enumerate(tqdm(os.listdir(args.dataset))):
            if not is_valid_path(os.path.join(args.dataset, aloha_demo)):
                continue

            aloha_demo_path = os.path.join(args.dataset, aloha_demo)

            with h5py.File(aloha_demo_path, "r") as aloha_hdf5:
                demo_number = aloha_demo.split("_")[1].split(".")[0]
                demo_i_group = data_group.create_group(f"demo_{demo_number}")
                if args.data_type == "hand":
                    demo_i_group['label'] = np.array([1])
                elif args.data_type == "robot":
                    demo_i_group['label'] = np.array([0])
                demo_i_group.attrs["num_samples"] = aloha_hdf5["action"].shape[0]
                demo_i_obs_group = demo_i_group.create_group("obs")

                # Extract the data from the aloha hdf5 file
                if args.arm == "right":
                    pass


                # obs
                demo_i_obs_group.create_dataset("front_img_1", data=aloha_hdf5["observations"]["images"]["cam_high"], dtype='uint8', chunks=(1, 480, 640, 3))
                demo_i_obs_group.create_dataset("right_wrist_img", data=aloha_hdf5["observations"]["images"]["cam_right_wrist"], dtype='uint8', chunks=(1, 480, 640, 3))
                demo_i_obs_group.create_dataset("joint_positions", data=aloha_hdf5["observations"]["qpos"][:, 7:])
                fk_positions = chain.forward_kinematics(torch.from_numpy(aloha_hdf5["observations"]["qpos"][:, 7:13]), end_only=True).get_matrix()[:, :3, 3]
                fk_positions = ee_pose_to_cam_frame(fk_positions, EXTRINSICS[args.extrinsics])[:, :3]

                demo_i_obs_group.create_dataset("ee_pose", data=fk_positions)

                # breakpoint()
                if args.data_type == "hand":
                    POINT_GAP=4
                    FUTURE_POINTS_COUNT=10
                elif args.data_type == "robot":
                    POINT_GAP=15
                    FUTURE_POINTS_COUNT=10

                future_traj_data = np.array([
                    get_future_points(fk_positions[j:], POINT_GAP, FUTURE_POINTS_COUNT) for j in range(len(fk_positions))
                ])

                # breakpoint()
                # actions
                demo_i_group.create_dataset("actions", data=future_traj_data)
                demo_i_group.create_dataset("actions_joints", data=aloha_hdf5["action"][:, 7:])
                fk_positions = chain.forward_kinematics(torch.from_numpy(aloha_hdf5["action"][:, 7:13]), end_only=True).get_matrix()[:, :3, 3]
                fk_positions = ee_pose_to_cam_frame(fk_positions, EXTRINSICS[args.extrinsics])[:, :3]
                demo_i_group.create_dataset("actions_xyz", data=fk_positions)



                # print(chain.forward_kinematics(torch.from_numpy(aloha_hdf5["observations"]["qpos"][10, 7:13])[None, :], end_only=True).get_matrix()[:, :3, 3])
                # print(convert_qpos_to_eef(aloha_hdf5["observations"]["qpos"][10, 7:13]))
                
                # create a dataset for the end effector positions
    
    # with h5py.File(args.out, "r", rdcc_nbytes=1024**2*2) as dataset:
    #     breakpoint()
    #     print("hi")

    split_train_val_from_hdf5(hdf5_path=args.out, val_ratio=0.2, filter_key=None)
    
        
    print("Successful Conversion!")