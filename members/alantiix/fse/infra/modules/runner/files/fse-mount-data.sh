#!/usr/bin/env bash
# Mount the fse data volume at /data (cloud-init runs this at first boot).
#
#   fse-mount-data.sh vol-0123456789abcdef0
#
# Waits for the attachment, creates an XFS filesystem only when the device has
# none (an existing filesystem is never formatted), adds one fstab entry by
# UUID with nofail, mounts it and creates the fse directories owned by the
# container user (uid 10001).
set -euo pipefail

if [[ $# -ne 1 || "$1" != vol-* ]]; then
  sed -n '2,9p' "$0"
  exit 2
fi

volume_id="$1"
mount_point="${FSE_DATA_DIR:-/data}"
device="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${volume_id//-/}"

waited=0
until [[ -e "$device" ]]; do
  if ((waited >= 300)); then
    echo "fse-mount-data: $device did not appear within 300s" >&2
    exit 1
  fi
  sleep 5
  waited=$((waited + 5))
done

fstype="$(blkid -o value -s TYPE "$device" || true)"
if [[ -z "$fstype" ]]; then
  echo "fse-mount-data: $device has no filesystem; creating xfs" >&2
  mkfs.xfs -L fsedata "$device"
  udevadm settle || true
else
  echo "fse-mount-data: $device already holds $fstype; not formatting" >&2
fi

uuid="$(blkid -o value -s UUID "$device")"
if [[ -z "$uuid" ]]; then
  echo "fse-mount-data: no UUID on $device" >&2
  exit 1
fi

mkdir -p "$mount_point"
if ! grep -q "^UUID=$uuid " /etc/fstab; then
  echo "UUID=$uuid $mount_point ${fstype:-xfs} defaults,nofail,noatime 0 2" >>/etc/fstab
fi
systemctl daemon-reload
mount -a

for dir in live-state live-bars recordings runs logs cache; do
  mkdir -p "$mount_point/$dir"
done
chown -R 10001:10001 "$mount_point"
