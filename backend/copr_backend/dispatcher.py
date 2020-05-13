"""
Abstract class Dispatcher for Build/Action dispatchers.
"""

import time
import multiprocessing
from setproctitle import setproctitle

from copr_backend.frontend import FrontendClient
from copr_backend.worker_manager import WorkerManager
from copr_backend.helpers import get_redis_logger, get_redis_connection


class Dispatcher(multiprocessing.Process):
    """
    1) Fetch tasks from frontend.
    2) Fill the WorkerManager queue.
    3) Run 'WorkerManager.run()'.
    4) Go to 1)

    See also:
    https://docs.pagure.org/copr.copr/developer_documentation/dispatchers.html
    https://docs.pagure.org/copr.copr/developer_documentation/worker_manager.html
    """

    # set to either 'action' or 'build' in sub-class
    task_type = 'task_type'

    # either ActionWorkerManager or BuildWorkerManager
    worker_manager_class = WorkerManager

    # how many background workers we let the WorkerManager start, by default
    # there's no limit
    max_workers = float("inf")

    # we keep track what build's newly appeared in the task list after fetching
    # the new set from frontend after get_frontend_tasks() call
    _previous_task_fetch_ids = set()

    def __init__(self, backend_opts):
        super().__init__(name=self.task_type + '-dispatcher')

        self.sleeptime = backend_opts.sleeptime
        self.opts = backend_opts
        logger_name = 'backend.{}_dispatcher'.format(self.task_type)
        logger_redis_who = '{}_dispatcher'.format(self.task_type)
        self.log = get_redis_logger(self.opts, logger_name, logger_redis_who)
        self.frontend_client = FrontendClient(self.opts, self.log)


    @classmethod
    def _update_process_title(cls, msg=None):
        proc_title = "{} dispatcher".format(cls.task_type.capitalize())
        if msg:
            proc_title += " - " + msg
        setproctitle(proc_title)

    def get_frontend_tasks(self):
        """
        Get _unfiltered_ list of tasks (QueueTask objects) from frontend (the
        set needs to contain both running and pending jobs).
        """
        raise NotImplementedError

    def _print_added_jobs(self, tasks):
        job_ids = {task.id for task in tasks}
        new_job_ids = job_ids - self._previous_task_fetch_ids
        if new_job_ids:
            self.log.info("Got new '%s' tasks: %s", self.task_type, new_job_ids)
        self._previous_task_fetch_ids = job_ids

    def run(self):
        """
        Starts the infinite task dispatching process.
        """
        self.log.info("%s dispatching started", self.task_type.capitalize())
        self._update_process_title()

        redis = get_redis_connection(self.opts)
        worker_manager = self.worker_manager_class(
            redis_connection=redis,
            log=self.log,
            max_workers=self.max_workers,
            frontend_client=self.frontend_client,
        )

        timeout = self.sleeptime
        while True:
            self._update_process_title("getting tasks from frontend")
            self.log.info("getting %ss from frontend", self.task_type)
            start = time.time()

            tasks = self.get_frontend_tasks()
            self._print_added_jobs(tasks)
            for task in tasks:
                worker_manager.add_task(task)

            # process the tasks
            self._update_process_title("processing tasks")
            worker_manager.run(timeout=timeout)

            sleep_more = timeout - (time.time() - start)
            if sleep_more > 0:
                time.sleep(sleep_more)

        # reset the title
        self._update_process_title()
