import os.path

from srl_stream.gym_world import create_single_world, default_arguments

from pybullet_planning.pybullet_tools.utils import pose_from_tform, get_pose, get_joint_name, get_joint_position, get_movable_joints
from utils import load_lisdf, test_is_robot


def load_lisdf_isaacgym(lisdf_dir, robots=True, pause=False, **kwargs):
    # TODO: Segmentation fault - possibly cylinders & mimic joints
    gym_world = create_single_world(args=default_arguments(use_gpu=False), spacing=5.)
    for name, path, scale, is_fixed, pose in load_lisdf(lisdf_dir, robots=robots, **kwargs):
        is_robot = test_is_robot(name)
        asset = gym_world.simulator.load_asset(
            asset_file=path, root=None, fixed_base=is_fixed or is_robot, #y_up=is_robot,
            gravity_comp=is_robot, collapse=False, vhacd=False)
        actor = gym_world.create_actor(asset, name=name, scale=scale)
        gym_world.set_pose(actor, pose_from_tform(pose))
    gym_world.simulator.update_viewer()
    if pause:
        gym_world.wait_if_gui()
    return gym_world

def update_gym_world(gym_world, pb_world, pause=False, verbose=False):
    for actor in gym_world.get_actors():
        name = gym_world.get_actor_name(actor)
        body = pb_world.name_to_body[name] # TODO: lookup if pb_world is None
        pose = get_pose(body)
        gym_world.set_pose(actor, pose)
        if verbose:
            print(f'Name: {name} | Actor: {actor} | Body: {body}')

        joint_state = {}
        for joint in get_movable_joints(body):
            joint_name = get_joint_name(body, joint)
            position = get_joint_position(body, joint)
            joint_state[joint_name] = position
            if verbose:
                print(f'Joint: {joint_name} | Position: {position}')
        joints = gym_world.get_joint_names(actor)
        positions = list(map(joint_state.get, joints))
        gym_world.set_joint_positions(actor, positions)
    gym_world.simulator.update_viewer()
    if pause:
        gym_world.wait_if_gui()

if __name__ == "__main__":
    lisdf_dir = '/home/caelan/Programs/interns/yang/kitchen-worlds/test_cases/tt_one_fridge_pick_2'
    load_lisdf_isaacgym(os.path.abspath(lisdf_dir))
