#!/usr/bin/env python3

import os
import re
import time
import glob
import signal
import logging
from enum import Enum
from threading import Event
from argparse import ArgumentParser
from typing import List

from prometheus_client import start_http_server
from prometheus_client.core import GaugeMetricFamily, REGISTRY, CounterMetricFamily

logging.basicConfig(
    filename='thread-exporter.log',
    filemode='w',
    format='%(asctime)s %(levelname)-8s %(message)s',
    level=logging.DEBUG)

logger = logging.getLogger()
shutdown_event = Event()


def shutdown_handler(signum, frame):
    logger.info('shutting down')
    shutdown_event.set()


signal.signal(signal.SIGTERM, shutdown_handler)
signal.signal(signal.SIGINT, shutdown_handler)


def time_it(func):
    def inner(*args, **kwargs):
        st = time.time()
        result = func(*args, **kwargs)
        logger.debug(f'Elapsed time for {func.__name__} : {time.time() - st}s')
        return result
    return inner


class Defaults(Enum):
    PORT = 9199


class Options:

    def __init__(self):
        self._parse_args()
        self._apply_env()
        self._show_active_options()

    def _parse_args(self):
        parser = ArgumentParser(
                description='Expose the cpu usage of given PID/children to Prometheus')
        parser.add_argument(
                '--filter',
                type=str,
                default='',
                help="filter pids - by name or a list of comma separated pid id's")
        parser.add_argument(
                '--port',
                type=int,
                default=Defaults.PORT.value,
                help=f'tcp port to bind to (default is {Defaults.PORT.value})')
        args = parser.parse_args()
        for attrib in args.__dict__:
            setattr(self, attrib, getattr(args, attrib))

    def _apply_env(self):
        if 'FILTER' in os.environ:
            self.filter = os.environ['FILTER']
        if 'PORT' in os.environ:
            self.port = os.environ['PORT']

    def _show_active_options(self):
        for attrib in self.__dict__:
            if  not attrib.startswith('_'):
                logger.info(f'Parameter: {attrib} = {getattr(self, attrib)}')


# def get_ticks() -> int:
#     clk_tck_pos = os.sysconf_names['SC_CLK_TCK']
#     return os.sysconf(clk_tck_pos)
# didn't end up using ticks. On modern Linux the clock is 100, so consumption would be 
# ticks/100...but since I'm charting in prometheus a % value, I felt this step wasn't 
# that necessary

@time_it
def filter_pids(filter: str, procfs: str = '/proc') -> List[str]:
    pid_list = []

    def _process_pids():
        all_pids = [pid_path[6:] for pid_path in glob.glob(f'{procfs}/*')
                    if pid_path[6:].isnumeric()]
        pids = filter.split(',')

        for pid in pids:
            if pid not in all_pids:
                logger.warning(f'No matching pid found for filter {pid}')
                continue
            pid_list.append(pid)
        return pid_list

    def _process_pid_names():
        # we need an offset to point to where to find the pid
        offset = len(procfs.split('/'))

        pid_cmd_list = glob.glob(f'{procfs}/*/comm')
        for com in pid_cmd_list:
            try:
                with open(com) as f:
                    if f.read().rstrip() == filter:
                        pid_list.append(com.split('/')[offset])
            except FileNotFoundError:
                # some pids may be shortlived and disappear between the glob and the
                # point at which we try to read the comm file
                continue

        return pid_list

    if ',' in filter or filter.isnumeric():
        return _process_pids()

    return _process_pid_names()


def read_file(file_path) -> str:
    with open(file_path, 'rb') as f:
        data = f.read()

    # replace null chars with spaces to make the returned string easier to parse
    return data.decode('utf-8').replace('\x00', ' ').rstrip()


class DaemonNames:
    """Handle cmdline parsing to return a daemon name"""

    @staticmethod
    def _extract_ceph(cmdline: str) -> str:
        # FIXME : this works for cephadm/baremetal but not for k8s/rook
        parts = cmdline.split(' ')
        _name_pos = parts.index('-n')
        return parts[_name_pos + 1]

    @classmethod
    def supported_daemons(cls) -> List[str]:
        return [func for func in dir(cls) if callable(getattr(cls, func)) and not func.startswith('_') and func != 'supported_daemons']

    @classmethod
    def ceph_osd(cls, cmdline: str) -> str:
        return DaemonNames._extract_ceph(cmdline)

    @classmethod
    def ceph_mon(cls, cmdline) -> str:
        return DaemonNames._extract_ceph(cmdline)
    
    @classmethod
    def ceph_mgr(cls, cmdline) -> str:
        return DaemonNames._extract_ceph(cmdline)
    
    @classmethod
    def rbd_mirror(cls, cmdline) -> str:
        return DaemonNames._extract_ceph(cmdline)
    
    @classmethod
    def ceph_mds(cls, cmdline) -> str:
        return DaemonNames._extract_ceph(cmdline)

class Process:
    def __init__(self, procfs, pid):
        self._procfs = procfs
        self.pid = pid
        self.pname = read_file(f'{self._procfs}/{pid}/comm')
        self._stat_fields = read_file(f'{self._procfs}/{pid}/stat').split(' ')

    @property
    def utime(self) -> float:
        return float(self._stat_fields[13])

    @property
    def stime(self) -> float:
        return float(self._stat_fields[14])


class Task(Process):
    def __init__(self, procfs, ppid, task_path):
        super().__init__(procfs, ppid)
        self.ppid = ppid
        self.tid = os.path.basename(task_path)
        self.tname = read_file(f'{task_path}/comm')
        self._stat_fields = read_file(f'{task_path}/stat').split(' ')


class ParentProcess(Process):
    def __init__(self, procfs, pid):
        super().__init__(procfs, pid)
        self.tasks = self._build_tasks()
        self.daemon_name = self._get_daemon_name()

    def _build_tasks(self) -> List[Task]:
        task_paths = glob.glob(f'{self._procfs}/{self.pid}/task/*')
        if not task_paths:
            return []
        return [Task(self._procfs, self.pid, task_path) for task_path in task_paths]

    def _get_daemon_name(self):
        try:
            func = getattr(DaemonNames, self.pname.replace('-', '_'))
        except AttributeError:
            return self.pname
        try:
            cmdline = read_file(f'{self._procfs}/{self.pid}/cmdline')
        except:
            return self.pname
        
        return func(cmdline) or self.pname


class CPUTimeCollector:
    def __init__(self, opts: ArgumentParser)-> None:
        self._opts = opts
        self._procfs = None
        self.metrics = {
            'perfscale_thread_cpu_user_seconds_total': CounterMetricFamily(
                'perfscale_thread_cpu_user_seconds_total',
                'user cpu time for a given thread',
                labels=[
                    'daemon',
                    'ppid',
                    'pname',
                    'tid',
                    'tname'
                ]
            ),
            'perfscale_thread_cpu_kernel_seconds_total': CounterMetricFamily(
                'perfscale_thread_cpu_kernel_seconds_total',
                'kernel/system cpu time for a given thread',
                labels=[
                    'daemon',                    
                    'ppid',
                    'pname',
                    'tid',
                    'tname'
                ]
            ),
            'perfscale_process_cpu_user_seconds_total': CounterMetricFamily(
                'perfscale_process_cpu_user_seconds_total',
                'user cpu time of a given process',
                labels=[
                    'daemon',                    
                    'pid',
                    'pname'
                ]
            ),
            'perfscale_process_cpu_kernel_seconds_total': CounterMetricFamily(
                'perfscale_process_cpu_kernel_seconds_total',
                'kernel/system cpu time of a given process',
                labels=[
                    'daemon',                    
                    'pid',
                    'pname'
                ]
            ),
        }

    @property
    def procfs(self):
        if not self._procfs:
            if glob.glob('/host/proc/*'):
                # when running as a container, expect to find the hosts /proc mounted 
                # at /host/proc, so if it has entries we use that for procfs
                self._procfs = '/host/proc'
            else:
                self._procfs = '/proc'
        return self._procfs

    def clear(self) -> None:
        for metric in self.metrics:
            self.metrics[metric].samples = []        

    @time_it
    def collect(self):
        self.clear()
        pid_list = filter_pids(self._opts.filter, self.procfs)
        if not pid_list:
            logger.error(f'No processes matching filter ({self._opts.filter}) detected')
        processes = [ParentProcess(self.procfs, pid) for pid in pid_list]
        for p in processes:
            self.metrics['perfscale_process_cpu_user_seconds_total'].add_metric([
                p.daemon_name,
                p.pid,
                p.pname], p.utime)
            self.metrics['perfscale_process_cpu_kernel_seconds_total'].add_metric([
                p.daemon_name,
                p.pid,
                p.pname], p.stime)                

            for t in p.tasks:
                self.metrics['perfscale_thread_cpu_user_seconds_total'].add_metric([
                    p.daemon_name,
                    p.pid,
                    p.pname,
                    t.tid,
                    t.tname], t.utime)

                self.metrics['perfscale_thread_cpu_kernel_seconds_total'].add_metric([
                    p.daemon_name,
                    p.pid,
                    p.pname,
                    t.tid,
                    t.tname], t.stime)

        for metric_name in self.metrics:
            yield self.metrics[metric_name]


def main() -> None:
    logger.info('thread-exporter starting')
    opts = Options()
    collector = CPUTimeCollector(opts)
    REGISTRY.register(collector)
    logger.info(f'exporter is using the {collector.procfs} filesystem for stats')
    logger.info(f'daemon name information provided for the following daemons;')
    for daemon_type in DaemonNames.supported_daemons():
        logger.info(f'- {daemon_type}')
    logger.info(f'Starting http server on port {opts.port}')
    start_http_server(opts.port)
    
    logger.info('thread-exporter started')
    while not shutdown_event.is_set():
        time.sleep(1)

    logger.info('thread-exporter stopped')


if __name__ == '__main__':
    main()
