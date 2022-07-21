# thread-exporter

Simple prometheus exporter to provide cpu stats for a multithreaded process or group of processes.

## Goals

The general idea behind the exporter is to provide some insight into where cpu is being consumed within a multithreaded daemon, helping to identify hot threads. In other words, it's not an exporter to monitor your day-to-day operations, but a tool you can use for analysis, focused on a specific process or group of processes.
## Installation

The requirements are pretty low. You'll need the following packages on your host;
* `python3`
* `python3-prometheus_client`

With these pre-requisites in place, just clone the repo to your machine. 

The exporter needs read access to the proc filesystem, since the cpu usage stats are extracted from `/proc/<pid>/stat`, which may mean running selinux in **permissive** mode.

### Building a local container

Alternatively, you can build the application as a container and run that. To build the container on your machine, just run the [build-thread-exporter.sh](buildah/build-thread-exporter.sh) script, and supply a version name (defaults to latest).

e.g.
```
cd buildah
./build-thread-exporter.sh 0.1
```

## Running the thread-exporter

### As a script
The exporter takes two parameters; 
```
usage: thread-exporter.py [-h] [--filter FILTER] [--port PORT]

Expose the cpu usage of given PID/children to Prometheus

options:
  -h, --help       show this help message and exit
  --filter FILTER  filter pids - by name or a list of comma separated pid id's
  --port PORT      tcp port to bind to (default is 9199)
``` 
The filter parameter is obviously the most important. This value can be the text of the command (the same as `/proc/<pid>/comm`) or it can be a comma separated list of specific PIDs to monitor.

eg.  
```
thread-exporter.py --filter Xorg
thread-exporter.py --filter 3240,6435
```

### As a container on a Host (not k8s)

Here's an example where the exporter is monitoring the Xorg daemon
```
podman run --rm -d --privileged --net host -e FILTER=Xorg -v /proc:/host/proc:ro localhost/thread-exporter:latest
```

Notes:
* the scripts options (filter and port) are configurable via environment variables
* The exporter accesses /proc like node-exporter, so it needs to run privileged. You may also have to set selinux to permissive.



## Metrics

The metrics returned by the exporter use the following labels; 

- daemon: daemon name (only ceph daemons are supported currently)
- pname: process name (from stat/comm)
- pid, ppid: parent PID
- tid: thread/task ID
- tname: thread name 


An example of the metrics exposed can be found in the examples directory ([metrics.txt](examples/metrics.txt)).
### Scrape Job Example

```
  - job_name: 'thread-analysis'
    scrape_interval: 5s
    static_configs:
    - targets: ['myhost.to-analyse.com:9199']
      labels:
        instance: 'myhost'
```

Using a relatively low scrape interval, helps to capture CPU spikes.
  
---  
  
## Ideas for Future work A.K.A the TODO list

1. update the DaemonNames class to extract the name correctly within k8s/rook
2. update logging to send log records to file and stdout for k8s deployments
3. create sample yaml for kubernetes deployment
4. Add tox to lint and mypy the code
5. Enhance the filter to accept multiple process names e.g. ceph-osd,rbd-mirror
6. add the collect time for the main functions to the metrics
7. add sample grafana dashboard
8. add a config file to hold the filter definition
9. make the daemon respond to a reload (SIGHUP), so you can update what is being tracked dynamically

