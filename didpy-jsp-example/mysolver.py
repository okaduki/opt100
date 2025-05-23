import re
from typing import Any

from discrete_optimization.generic_tools.do_problem import Solution
from discrete_optimization.generic_tools.do_solver import WarmstartMixin
from discrete_optimization.generic_tools.dyn_prog_tools import DpSolver, dp
from discrete_optimization.jsp.problem import JobShopProblem, JobShopSolution


class DpJspSolver(DpSolver, WarmstartMixin):
    hyperparameters = DpSolver.hyperparameters
    problem: JobShopProblem

    def init_model(self, **kwargs: Any) -> None:
        model = dp.Model()
        jobs = []
        durations = []
        machines = []
        job_id = []
        cur_sub_job_per_jobs = {i: 0 for i in range(self.problem.n_jobs)}
        index = {}
        len_ = 0
        while len_ < self.problem.n_all_jobs:
            for i in range(self.problem.n_jobs):
                if cur_sub_job_per_jobs[i] < len(self.problem.list_jobs[i]):
                    jobs.append((i, cur_sub_job_per_jobs[i]))
                    durations.append(
                        self.problem.list_jobs[i][
                            cur_sub_job_per_jobs[i]
                        ].processing_time
                    )
                    machines.append(
                        self.problem.list_jobs[i][cur_sub_job_per_jobs[i]].machine_id
                    )
                    job_id.append(i)
                    index[(i, cur_sub_job_per_jobs[i])] = len_
                    cur_sub_job_per_jobs[i] += 1
                    len_ += 1
        precedence_by_index = [set() for i in range(self.problem.n_all_jobs)]
        for i in range(self.problem.n_jobs):
            for j in range(1, len(self.problem.list_jobs[i])):
                ind = index[(i, j)]
                ind_pred = index[(i, j - 1)]
                precedence_by_index[ind].add(ind_pred)

        task = model.add_object_type(number=self.problem.n_all_jobs)
        done = model.add_set_var(object_type=task, target=set())
        undone = model.add_set_var(
            object_type=task, target=range(self.problem.n_all_jobs)
        )
        cur_time_per_machine = [
            model.add_int_var(target=0) for m in range(self.problem.n_machines)
        ]
        cur_time_per_job = [
            model.add_int_var(target=0) for m in range(self.problem.n_jobs)
        ]

        # dp[U][t][m1][m2]...[j1][j2]... := 未処理のタスク集合U, ジョブの最遅終了時刻t, M1 の終了時刻がm1, ..., ジョブ1の終了時刻がj1, ... のときのmakespan の最小値
        # dp[U,t,m1,..., j1,...] =
        #   min_{J_i_j \in U, J_i_{j-1} \not\in U}(
        #       t' - t + dp[U \ J_i_j, t', m1, ..., m'_p, ..., j1, ..., j'_j, ...)
        #   )
        #   where
        #       p = machine[i][j]
        #       m'_p = max(m_p + C_{i,j}, j_j + C_{i,j})
        #       j'_j = max(m_p + C_{i,j}, j_j + C_{i,j})
        #       t' = max(t, m_p + C_{i,j}, j_j + C_{i,j})

        finish = model.add_int_var(0)
        cur_time_total = model.add_int_resource_var(target=0, less_is_better=True)
        model.add_base_case([finish == 1, undone.is_empty()])
        self.transitions = {}
        for i in range(len(jobs)):
            m = machines[i]
            dur = durations[i]
            jid = job_id[i]
            sched = dp.Transition(
                name=f"sched_{i}",
                cost=(  # dp.max(cur_time_per_machine[m]-cur_time_per_job[jid],
                    #        -cur_time_per_machine[m]+cur_time_per_job[jid])
                    dp.max(
                        cur_time_total,
                        dp.max(
                            cur_time_per_machine[m] + dur, cur_time_per_job[jid] + dur
                        ),
                    )
                    - cur_time_total
                )
                + dp.IntExpr.state_cost(),
                # cost=dp.IntExpr.state_cost(),
                effects=[
                    (done, done.add(i)),
                    (undone, undone.remove(i)),
                    (
                        cur_time_per_job[jid],
                        dp.max(
                            cur_time_per_machine[m] + dur, cur_time_per_job[jid] + dur
                        ),
                    ),
                    (
                        cur_time_per_machine[m],
                        dp.max(
                            cur_time_per_machine[m] + dur, cur_time_per_job[jid] + dur
                        ),
                    ),
                    (
                        cur_time_total,
                        dp.max(
                            cur_time_total,
                            dp.max(
                                cur_time_per_machine[m] + dur,
                                cur_time_per_job[jid] + dur,
                            ),
                        ),
                    ),
                ],
                preconditions=[
                    undone.contains(i),
                ]
                + [done.contains(j) for j in precedence_by_index[i]],
            )
            model.add_transition(sched)
            self.transitions[i] = sched
        finish = dp.Transition(
            name="finish_",
            effects=[(finish, 1)],
            # cost=cur_time_total+dp.IntExpr.state_cost(),
            cost=dp.IntExpr.state_cost(),
            preconditions=[done.len() == self.problem.n_all_jobs],
        )
        model.add_transition(finish)
        self.transitions["finish"] = finish
        self.jobs = jobs
        self.prec = precedence_by_index
        self.index = index
        self.machines = machines
        self.duration = durations
        self.model = model
        self.cur_time_per_machine = cur_time_per_machine
        self.cur_time_per_job = cur_time_per_job

    def retrieve_solution(self, sol: dp.Solution) -> Solution:
        def extract_ints(word):
            return tuple(int(num) for num in re.findall(r"\d+", word))

        schedule_per_machine = {m: [] for m in range(self.problem.n_machines)}
        schedules = {}
        state = self.model.target_state

        for transition in sol.transitions:
            state = transition.apply(state, self.model)
            if "finish" not in transition.name:
                t_number = extract_ints(transition.name)[0]
                m = self.machines[t_number]
                j = self.jobs[t_number]
                start = 0
                if len(schedule_per_machine[m]) > 0:
                    start = max(start, schedule_per_machine[m][-1][1])
                if j[1] > 0:
                    start = max(start, schedules[(j[0], j[1] - 1)][1])
                end = start + self.duration[t_number]
                schedule_per_machine[m].append((start, end))
                schedules[j] = (start, end)
        sol = JobShopSolution(
            problem=self.problem,
            schedule=[
                [schedules[(i, j)] for j in range(len(self.problem.list_jobs[i]))]
                for i in range(self.problem.n_jobs)
            ],
        )
        return sol

    def set_warm_start(self, solution: JobShopSolution) -> None:
        initial_solution = []
        flatten_schedule = [
            (i, solution.schedule[self.jobs[i][0]][self.jobs[i][1]])
            for i in range(len(self.jobs))
        ]
        sorted_flatten = sorted(flatten_schedule, key=lambda x: (x[1][0], x[1][1]))
        for index, _ in sorted_flatten:
            initial_solution.append(self.transitions[index])
        initial_solution.append(self.transitions["finish"])
        self.initial_solution = initial_solution


class DpJspSolver2(DpSolver, WarmstartMixin):
    hyperparameters = DpSolver.hyperparameters
    problem: JobShopProblem

    def init_model(self, **kwargs: Any) -> None:
        n_jobs = self.problem.n_jobs
        model = dp.Model()
        jobs = []
        durations = []
        machines = []
        job_id = []
        cur_sub_job_per_jobs = {i: 0 for i in range(n_jobs)}
        index = {}
        len_ = 0

        while len_ < self.problem.n_all_jobs:
            for i in range(n_jobs):
                if cur_sub_job_per_jobs[i] < len(self.problem.list_jobs[i]):
                    jobs.append((i, cur_sub_job_per_jobs[i]))
                    durations.append(
                        self.problem.list_jobs[i][
                            cur_sub_job_per_jobs[i]
                        ].processing_time
                    )
                    machines.append(
                        self.problem.list_jobs[i][cur_sub_job_per_jobs[i]].machine_id
                    )
                    job_id.append(i)
                    index[(i, cur_sub_job_per_jobs[i])] = len_
                    cur_sub_job_per_jobs[i] += 1
                    len_ += 1
        precedence_by_index = [set() for i in range(self.problem.n_all_jobs)]
        for i in range(n_jobs):
            for j in range(1, len(self.problem.list_jobs[i])):
                ind = index[(i, j)]
                ind_pred = index[(i, j - 1)]
                precedence_by_index[ind].add(ind_pred)

        task = model.add_object_type(number=self.problem.n_all_jobs)

        # 簡単化のためサブタスクの数はすべてのジョブで同じとする
        n_subjob = len(self.problem.list_jobs[0])
        for job in self.problem.list_jobs:
            assert len(job) == n_subjob
        subtask = model.add_object_type(number=n_subjob + 1)

        cur_time_per_machine = [
            model.add_int_resource_var(target=0, less_is_better=True)
            for m in range(self.problem.n_machines)
        ]
        cur_time_per_job = [
            model.add_int_resource_var(target=0, less_is_better=True)
            for m in range(n_jobs)
        ]

        next_task_per_job = [
            model.add_element_resource_var(
                object_type=subtask, target=0, less_is_better=False
            )
            for m in range(n_jobs)
        ]

        job_bounds = []
        for i in range(n_jobs):
            arr = [0] * (n_subjob + 1)
            for j in range(n_subjob - 1, -1, -1):
                arr[j] = arr[j + 1] + self.problem.list_jobs[i][j].processing_time
            table = model.add_int_table(arr)
            job_bounds.append(table)

        # dp[l1][l2]...[t][m1][m2]...[j1][j2]... := ジョブ1 の次のタスクがl1,..., ジョブの最遅終了時刻t, M1 の終了時刻がm1, ..., ジョブ1の終了時刻がj1, ... のときのmakespan の最小値
        # dp[l1,...,t,m1,..., j1,...] =
        #   min_{J_{i,j} | l_i == j }(
        #       t' - t + dp[..., l_i+1, ..., t', m1, ..., m'_p, ..., j1, ..., j'_j, ...)
        #   )
        #   where
        #       p = machine[i][j]
        #       end = max(m_p + C_{i,j}, j_j + C_{i,j})
        #       t = max([m1, m2, ..., j1, j2])
        #       m'_p = end
        #       j'_j = end
        #       t' = max(t, end)

        def reduction_max(exprs):
            l = 1
            n = len(exprs)
            while l < n:
                for i in range(0, n, l * 2):
                    if i + l < n:
                        exprs[i] = dp.max(exprs[i], exprs[i + l])
                l *= 2
            return exprs[0]

        finish = model.add_int_var(0)
        cur_time_total = model.add_int_resource_var(target=0, less_is_better=True)
        # cur_time_total = reduction_max(cur_time_per_job + cur_time_per_machine)

        model.add_base_case([finish == 1])
        self.transitions = {}
        for i in range(len(jobs)):
            m = machines[i]
            dur = durations[i]
            jid = job_id[i]
            subjob_id = jobs[i][1]

            end_time = dp.max(
                cur_time_per_machine[m] + dur, cur_time_per_job[jid] + dur
            )
            nt = dp.max(cur_time_total, end_time)

            sched = dp.Transition(
                name=f"sched_{i}",
                cost=dp.max(nt, dp.IntExpr.state_cost()),
                effects=[
                    (next_task_per_job[jid], next_task_per_job[jid] + 1),
                    (cur_time_per_job[jid], end_time),
                    (cur_time_per_machine[m], end_time),
                    (cur_time_total, nt),
                ],
                preconditions=[next_task_per_job[jid] == subjob_id],
            )
            model.add_transition(sched)
            self.transitions[i] = sched
        finish_transition = dp.Transition(
            name="finish_",
            effects=[(finish, 1)],
            cost=dp.IntExpr.state_cost(),
            preconditions=[
                finish == 0,
                sum(next_task_per_job) == n_jobs * n_subjob,
            ],
        )
        model.add_transition(finish_transition, forced=True)

        # dual bound for dp[l1,...] = \max_{0 <= i < n} \sum_{l_i <= j < m} p_{i,j}
        if False:
            expr = job_bounds[0][next_task_per_job[0]]
            for jid in range(1, self.problem.n_jobs):
                expr = dp.max(expr, job_bounds[jid][next_task_per_job[jid]])
            model.add_dual_bound(expr)
        else:  # faster 7 secs
            exprs = [job_bounds[jid][next_task_per_job[jid]] for jid in range(n_jobs)]
            expr = reduction_max(exprs)
            model.add_dual_bound(expr)

        self.transitions["finish"] = finish_transition
        self.jobs = jobs
        self.prec = precedence_by_index
        self.index = index
        self.machines = machines
        self.duration = durations
        self.model = model
        self.cur_time_per_machine = cur_time_per_machine
        self.cur_time_per_job = cur_time_per_job

    def retrieve_solution(self, sol: dp.Solution) -> Solution:
        if sol.cost is None:
            raise RuntimeError("Solution not found.")

        def extract_ints(word):
            return tuple(int(num) for num in re.findall(r"\d+", word))

        schedule_per_machine = {m: [] for m in range(self.problem.n_machines)}
        schedules = {}
        state = self.model.target_state

        for transition in sol.transitions:
            state = transition.apply(state, self.model)
            if "finish" not in transition.name:
                t_number = extract_ints(transition.name)[0]
                m = self.machines[t_number]
                j = self.jobs[t_number]
                start = 0
                if len(schedule_per_machine[m]) > 0:
                    start = max(start, schedule_per_machine[m][-1][1])
                if j[1] > 0:
                    start = max(start, schedules[(j[0], j[1] - 1)][1])
                end = start + self.duration[t_number]
                schedule_per_machine[m].append((start, end))
                schedules[j] = (start, end)
        sol = JobShopSolution(
            problem=self.problem,
            schedule=[
                [schedules[(i, j)] for j in range(len(self.problem.list_jobs[i]))]
                for i in range(self.problem.n_jobs)
            ],
        )
        return sol

    def set_warm_start(self, solution: JobShopSolution) -> None:
        initial_solution = []
        flatten_schedule = [
            (i, solution.schedule[self.jobs[i][0]][self.jobs[i][1]])
            for i in range(len(self.jobs))
        ]
        sorted_flatten = sorted(flatten_schedule, key=lambda x: (x[1][0], x[1][1]))
        for index, _ in sorted_flatten:
            initial_solution.append(self.transitions[index])
        initial_solution.append(self.transitions["finish"])
        self.initial_solution = initial_solution
