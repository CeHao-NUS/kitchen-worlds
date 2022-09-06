#!/usr/bin/env python

from __future__ import print_function
import os
import json
import pickle
import shutil
from os import listdir
from os.path import join, abspath, dirname, isdir, isfile
from tabnanny import verbose
from config import EXP_PATH
import numpy as np
import random
import time
import argparse

from pybullet_tools.pr2_utils import get_group_conf
from pybullet_tools.utils import disconnect, LockRenderer, has_gui, WorldSaver, wait_if_gui, \
    SEPARATOR, get_aabb, wait_for_duration, safe_remove, ensure_dir, reset_simulation
from pybullet_tools.bullet_utils import summarize_facts, print_goal, nice, get_datetime
from pybullet_tools.pr2_agent import get_stream_info, post_process, move_cost_fn, \
    get_stream_map, solve_multiple, solve_one
from pybullet_tools.logging import TXT_FILE

from pybullet_tools.pr2_primitives import get_group_joints, Conf, get_base_custom_limits, Pose, Conf, \
    get_ik_ir_gen, get_motion_gen, get_cfree_approach_pose_test, get_cfree_pose_pose_test, get_cfree_traj_pose_test, \
    get_grasp_gen, Attach, Detach, Clean, Cook, control_commands, Command, \
    get_gripper_joints, GripperCommand, State

from pddlstream.language.constants import Equal, AND, print_solution, PDDLProblem
from pddlstream.algorithms.meta import solve, DEFAULT_ALGORITHM
from pddlstream.algorithms.constraints import PlanConstraints
from pddlstream.algorithms.algorithm import reset_globals
from pddlstream.algorithms.common import SOLUTIONS
from pddlstream.utils import read, INF, get_file_path, find_unique, Profiler, str_from_object, TmpCWD

from lisdf_tools.lisdf_loader import load_lisdf_pybullet, pddlstream_from_dir
from lisdf_tools.lisdf_planning import pddl_to_init_goal, Problem

from world_builder.actions import apply_actions

from mamao_tools.utils import get_feasibility_checker


SKIP_IF_SOLVED = False
SKIP_IF_SOLVED_RECENTLY = True

TASK_NAME = 'tt_one_fridge_pick'
TASK_NAME = 'tt_one_fridge_table_in'
TASK_NAME = 'tt_two_fridge_in'

PARALLEL = False
FEASIBILITY_CHECKER = 'None'  ## None | oracle

parser = argparse.ArgumentParser()
parser.add_argument('-t', type=str, default=TASK_NAME)
parser.add_argument('-f', type=str, default=FEASIBILITY_CHECKER)
parser.add_argument('-p', action='store_true', default=PARALLEL)
args = parser.parse_args()

TASK_NAME = args.t
PARALLEL = args.p
FEASIBILITY_CHECKER = args.f

DATABASE_DIR = join('..', '..', 'mamao-data', TASK_NAME)

def init_experiment(exp_dir):
    if isfile(TXT_FILE):
        os.remove(TXT_FILE)

#####################################


def run_one(run_dir, parallel=False, task_name=TASK_NAME, SKIP_IF_SOLVED=SKIP_IF_SOLVED):
    ori_dir = run_dir ## join(DATABASE_DIR, run_dir)
    file = join(ori_dir, f'plan_rerun_fc={FEASIBILITY_CHECKER}.json')
    if isfile(file):
        if SKIP_IF_SOLVED:
            print('skipping solved problem', run_dir)
            return
        elif SKIP_IF_SOLVED_RECENTLY:
            last_modified = os.path.getmtime(file)
            if time.time() - last_modified < 60 * 60 * 24:
                print('skipping recently solved problem', run_dir)
                return

    print(f'\n\n\n--------------------------\n    rerun {ori_dir} \n------------------------\n\n\n')
    run_name = os.path.basename(ori_dir)
    exp_dir = join(EXP_PATH, f"{task_name}_{run_name}")
    if isdir(exp_dir):
        shutil.rmtree(exp_dir)
    if not isdir(exp_dir):
        shutil.copytree(ori_dir, exp_dir)

    if False:
        from utils import load_lisdf_synthesizer
        scene = load_lisdf_synthesizer(exp_dir)

    world = load_lisdf_pybullet(exp_dir, width=720, height=560, verbose=False)
    saver = WorldSaver()
    problem = Problem(world)

    if False:
        from utils import load_lisdf_nvisii
        scene = load_lisdf_nvisii(exp_dir)

    pddlstream_problem = pddlstream_from_dir(problem, exp_dir=exp_dir, collisions=True, teleport=False)
    world.summarize_all_objects()

    stream_info = world.robot.get_stream_info(partial=False, defer=False)
    _, _, _, stream_map, init, goal = pddlstream_problem
    summarize_facts(init, world=world)
    print_goal(goal)
    print(SEPARATOR)
    init_experiment(exp_dir)

    fc = get_feasibility_checker(ori_dir, mode=FEASIBILITY_CHECKER)

    start = time.time()
    if parallel:
        solution = solve_multiple(pddlstream_problem, stream_info)
    else:
        solution = solve_one(pddlstream_problem, stream_info, fc)
    planning_time = time.time() - start
    saver.restore()

    print_solution(solution)
    plan, cost, evaluations = solution
    if (plan is None) or not has_gui():
        disconnect()
        return

    print(SEPARATOR)
    with LockRenderer(lock=True):
        commands = post_process(problem, plan)
        problem.remove_gripper()
        saver.restore()

    """ log plan, planning stats, and commands """
    with open(join(ori_dir, f'plan_rerun_fc={FEASIBILITY_CHECKER}.json'), 'w') as f:
        data = {
            'planning_time': planning_time,
            'plan': [[str(a.name)]+[str(v) for v in a.args] for a in plan],
            'datatime': get_datetime(),
        }
        json.dump(data, f, indent=3)
    with open(join(ori_dir, f'commands_rerun_fc={FEASIBILITY_CHECKER}.txt'), 'w') as f:
        f.write('\n'.join([str(n) for n in commands]))
    # with open(join(ori_dir, 'commands_rerun.pkl'), 'wb') as f:
    #     pickle.dump(commands, f, pickle.HIGHEST_PROTOCOL)

    saver.restore()
    apply_actions(problem, commands, time_step=0.01)

    # disconnect()
    reset_simulation()
    shutil.rmtree(exp_dir)


def process(index, parallel=True):
    np.random.seed(int(time.time()))
    random.seed(time.time())
    return run_one(str(index), parallel=parallel)


def main(parallel=True):
    if isdir('visualizations'):
        shutil.rmtree('visualizations')

    start_time = time.time()
    cases = [join(DATABASE_DIR, f) for f in listdir(DATABASE_DIR) if isdir(join(DATABASE_DIR, f))]
    cases.sort()
    # cases = [f for f in cases if '/6' in f or '/7' in f or '/8' in f or '/9' in f]

    num_cases = len(cases)
    if parallel:
        import multiprocessing
        from multiprocessing import Pool

        max_cpus = 24
        num_cpus = min(multiprocessing.cpu_count(), max_cpus)
        print(f'using {num_cpus} cpus')
        with Pool(processes=num_cpus) as pool:
            # for result in pool.imap_unordered(process, range(num_cases)):
            #     pass
            pool.map(process, cases)
            # pool.map(process, range(num_cases))

    else:
        for i in range(num_cases):
            # if i in [0, 1]: continue
            process(cases[i], parallel=False)

    print(f'solved {num_cases} problems (parallel={parallel}) in {round(time.time() - start_time, 3)} sec')


if __name__ == '__main__':
    main(parallel=PARALLEL)
