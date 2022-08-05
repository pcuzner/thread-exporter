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
./build-thread-exporter.sh 0.2
```

| :bulb: Depending on your OS, you may need to build with root/sudo. On Fedora 36, rootless builds (and rootless podman!) seem to work fine. |
|-----|

## Running the thread-exporter

### As a script
The exporter takes two parameters; 
```
usage: thread-exporter.py [-h] [--filter FILTER] [--port PORT]

Expose the cpu usage of given PID/children to Prometheus

options:
  -h, --help       show this help message and exit
  --filter FILTER  filter pids by name or pid (can also be a comma separated list)
  --port PORT      tcp port to bind to (default is 9199)
``` 
The filter parameter is obviously the most important. This value can be the text of the command (the same as `/proc/<pid>/comm`), a pid number or a comma separated list of pids or names or a mix of both.

eg.  
```
thread-exporter.py --filter Xorg
thread-exporter.py --filter 3240,gnome-shell
```

### As a container on a Host (not k8s)

Here's an example where the exporter is monitoring the Xorg daemon
```
podman run --rm -d --privileged --net host -e FILTER=Xorg -v /proc:/host/proc:ro localhost/thread-exporter:latest
```

Notes:
* the scripts options (filter and port) are configurable via environment variables
* The exporter accesses /proc like node-exporter, so it needs to run privileged. You may also have to set selinux to permissive.

### As a Daemonset in Openshift 4.x

The examples folder provides 4 yaml files;
| filename | Description |
|------------|---------------|
| thread-exporter-aio.yml | Single file that creates all required resources (pick me!)|
| thread-exporter.yml | create the Daemonset for the thread-exporter |
| thread-exporter-service.yml | create the Service resource (endpoint definition) |
| thread-exporter-service-monitor.yml | create the ServiceMonitor resource for scraping the endpoint |

| :bulb: If the perfscale_* metrics aren't showing up in  Openshift's Prometheus, check that the namespace you're using has the `openshift.io/cluster-monitoring` label set to `true`|
|-----|

Before you deploy, decide which process(es) you need data for and declare them in the spec.containers.env section. The example provided shows ceph-osd, but this could be any process name (as defined in `/proc/<pid>/comm`)


To deploy, all you really need is the '*aio*' version
```
# oc create -f thread-exporter-aio.yml
```

Once deployed you should see something like this;

```
[root@pcuzner-build thread-exporter]# oc get all -l app=thread-exporter
NAME                        READY   STATUS    RESTARTS   AGE
pod/thread-exporter-4dlp4   1/1     Running   0          17h
pod/thread-exporter-9s25r   1/1     Running   0          17h
pod/thread-exporter-g6d9m   1/1     Running   0          17h
pod/thread-exporter-hvld8   1/1     Running   0          17h
pod/thread-exporter-rrjv4   1/1     Running   0          17h

NAME                              TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
service/thread-exporter-service   ClusterIP   172.30.11.101   <none>        9199/TCP   19h

NAME                             DESIRED   CURRENT   READY   UP-TO-DATE   AVAILABLE   NODE SELECTOR   AGE
daemonset.apps/thread-exporter   5         5         5       5            5           <none>          17h

```

You should start seeing metrics appear within 1-2 minutes.

The host's /proc filesystem is normally not accessible to containers, but thread-exporter needs access to read the `/proc/*/stat/*` files. To grant access to /proc, the thread-exporter container runs as a privileged container, using the built-in node-exporter `ServiceAccount` (and associated node-exporter SCC).

To remove the thread-exporter, you can delete the resources individually, or if you created them using the 'aio' yml, just use that.

```
# oc delete -f thread-exporter-aio.yml
```

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

- [X] update the DaemonNames class to extract the name correctly within k8s/rook
- [ ] update logging to send log records to file and stdout for k8s deployments
- [X] create sample yaml for kubernetes deployment
- [ ] add tox to lint and mypy the code
- [ ] add the collect time for the main functions to the metrics
- [ ] add sample grafana dashboard
- [ ] add a config file to hold the filter definition
- [ ] make the daemon respond to a reload (SIGHUP), so you can update what is being tracked dynamically
- [X] catch filenotfound on readfile - i.e. osd restarts
- [ ] add tests! Finding pids and daemon name extract are bare minimums!


