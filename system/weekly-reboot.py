#!/usr/bin/python3
"""Weekly reboot guard; never remove package locks or force a shutdown."""
import argparse
import fcntl
import os
from pathlib import Path
import subprocess
import time

LOCK_PATHS = tuple(map(Path, (
    '/var/lib/dpkg/lock-frontend', '/var/lib/dpkg/lock',
    '/var/lib/apt/lists/lock', '/var/cache/apt/archives/lock',
    '/run/unattended-upgrades.lock',
)))


def pending_locks(paths=LOCK_PATHS):
    """Probe the actual POSIX locks used by APT, not merely lock-file existence."""
    descriptors, busy = [], []
    try:
        for path in paths:
            try:
                fd = os.open(path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW)
            except FileNotFoundError:
                continue
            descriptors.append(fd)
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                busy.append(str(path))
        return busy
    finally:
        for fd in descriptors:
            os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check-only', action='store_true', help='Check locks without scheduling or performing a reboot.')
    args = parser.parse_args(argv)
    if args.check_only:
        busy = pending_locks()
        print('Package operations busy: '+', '.join(busy) if busy else 'No package operation holds a checked lock. No reboot requested.', flush=True)
        return
    deadline = time.monotonic() + 3600
    while busy := pending_locks():
        if time.monotonic() >= deadline:
            raise SystemExit('Package operation still busy after one hour; weekly reboot skipped without forcing it.')
        print('Waiting for package operations: '+', '.join(busy), flush=True)
        time.sleep(30)
    print('Requesting the scheduled weekly reboot.', flush=True)
    # The distribution shutdown handler remains enabled to stop unattended-upgrades
    # gracefully if a new operation starts after the lock check.
    subprocess.run(['/usr/bin/systemctl', '--no-block', 'reboot'], check=True)


if __name__ == '__main__':
    main()
