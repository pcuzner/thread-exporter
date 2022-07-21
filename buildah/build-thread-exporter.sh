#!/usr/bin/bash
# use buildah to create a container holding the thread-exporter

if [ ! -z "$1" ]; then
  TAG=$1
else
  TAG='latest'
fi

echo "Build image with the tag: $TAG"

IMAGE="alpine:edge"

container=$(buildah from $IMAGE)
#mountpoint=$(buildah mount $container)
buildah run $container apk add python3
buildah run $container apk add py3-prometheus-client

buildah run $container mkdir -p /host/proc
buildah copy $container ../thread-exporter.py /thread-exporter.py
buildah run $container chmod ug+x /thread-exporter.py

# entrypoint
buildah config --entrypoint "./thread-exporter.py" $container

# finalize
buildah config --label maintainer="Paul Cuzner <pcuzner@redhat.com>" $container
buildah config --label description="thread-exporter" $container
buildah config --label summary="thread-exporter is a prometheus exporter that exposes per thread cpu usage for a given process(es)" $container
buildah commit --format docker --squash $container thread-exporter:$TAG
