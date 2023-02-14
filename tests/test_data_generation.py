#!/usr/bin/env python

from __future__ import print_function
import shutil
import pickle
import os
import time
import random
import json
from os.path import join, abspath, dirname, isdir, isfile, basename
from config import EXP_PATH, OUTPUT_PATH

from pddlstream.language.constants import Equal, AND, print_solution, PDDLProblem
from pddlstream.algorithms.meta import solve, create_parser

from pybullet_tools.utils import disconnect, LockRenderer, has_gui, WorldSaver, wait_if_gui, \
    SEPARATOR, get_aabb, wait_for_duration, has_gui, reset_simulation, set_random_seed, \
    set_numpy_seed, set_renderer
from pybullet_tools.bullet_utils import summarize_facts, print_goal, nice, get_datetime
from pybullet_tools.pr2_agent import solve_multiple, post_process, pddlstream_from_state_goal, \
    create_cwd_saver
from pybullet_tools.pr2_primitives import control_commands, State, apply_commands
from pybullet_tools.logging import parallel_print, myprint

from lisdf_tools.lisdf_loader import pddl_files_from_dir

from world_builder.actions import apply_actions
from world_builder.world_generator import save_to_outputs_folder

from test_utils import parallel_processing, get_config
from test_world_builder import create_pybullet_world


config = get_config('kitchen_mini_feg.yaml')


#####################################


def process(index):
    """ exist a version in cognitive-architectures for generating mini-datasets (single process),
        run in kitchen-worlds for parallelization, but no reliable planning time data

        inside each data folder, to be generated:
        - before planning:
            [x] scene.lisdf
            [x] problem.pddl
            [x] planning_config.json
            [x] log.txt (generated before planning)
        - after planning:
            [x] plan.json
            [x] commands.pkl
            [x] log.json (generated by pddlstream)
    """

    seed = config.seed
    if seed is None:
        seed = random.randint(0, 10 ** 6 - 1)
    set_random_seed(seed)
    set_numpy_seed(seed)
    print('Seed:', seed)

    exp_dir = join(config.data.out_dir, get_datetime(TO_LISDF=True))

    """ STEP 1 -- GENERATE SCENES """
    world, goal = create_pybullet_world(config, SAVE_LISDF=False, SAVE_TESTCASE=True)
    saver = WorldSaver()

    domain_path, stream_path, config_path = pddl_files_from_dir(exp_dir, replace_pddl=False)
    cwd_saver = create_cwd_saver()
    print_fn = parallel_print ## if args.parallel else myprint
    print_fn(config)

    state = State(world)
    pddlstream_problem = pddlstream_from_state_goal(
        state, goal, domain_pddl=domain_path, stream_pddl=stream_path,
        custom_limits=world.robot.custom_limits, collisions=not config.cfree,
        teleport=config.teleport, print_fn=print_fn)
    stream_info = world.robot.get_stream_info()

    kwargs = {'visualize': True}
    if config.diverse:
        kwargs.update(dict(
            diverse=True,
            downward_time=20,  ## max time to get 100, 10 sec, 30 sec for 300
            evaluation_time=60,  ## on each skeleton
            max_plans=200,  ## number of skeletons
        ))
    start = time.time()
    solution, tmp_dir = solve_multiple(pddlstream_problem, stream_info, lock=not args.enable,
                                       cwd_saver=cwd_saver, **kwargs)

    print_solution(solution)
    plan, cost, evaluations = solution

    """ =============== log plan and planning time =============== """
    t = None if config.parallel else round(time.time() - start, 3)
    if plan is None:
        plan_log = None
        plan_len = None
        init = None
    else:
        plan_log = [str(a) for a in plan]
        plan_len = len(plan)
        init = [[str(a) for a in f] for f in evaluations.preimage_facts]
    time_log = [{
        'planning': t, 'plan': plan_log, 'plan_len': plan_len, 'init': init
    }, {'total_planning': t}]
    with open(join(exp_dir, f'plan.json'), 'w') as f:
        json.dump(time_log, f, indent=4)

    """ =============== save planing log =============== """
    txt_file = join(tmp_dir, 'txt_file.txt')
    if isfile(txt_file):
        shutil.move(txt_file, join(exp_dir, f"log.txt"))
    txt_file = join(tmp_dir, 'visualizations', 'log.json')
    if isfile(txt_file):
        shutil.move(txt_file, join(exp_dir, f"log.json"))
    cwd_saver.restore()

    """ =============== save commands for replay =============== """
    with LockRenderer(lock=config.lock):
        commands = post_process(state, plan)
        state.remove_gripper()
        saver.restore()
    with open(join(exp_dir, f"commands.pkl"), 'wb') as f:
        pickle.dump(commands, f)

    """ =============== visualize the plan =============== """
    if (plan is None) or not has_gui():
        reset_simulation()
        disconnect()
        return

    print(SEPARATOR)
    saver.restore()
    # wait_if_gui('Execute?')
    if config.simulate:  ## real physics
        control_commands(commands)
    else:
        set_renderer(True)
        apply_actions(state, commands, time_step=1e-2, verbose=False)
    # wait_if_gui('Finish?')
    print(SEPARATOR)
    reset_simulation()
    disconnect()


def collect_for_fastamp():
    parallel_processing(process, range(config.n_data), parallel=config.parallel)


if __name__ == '__main__':
    collect_for_fastamp()
