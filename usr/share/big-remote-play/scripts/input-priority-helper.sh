#!/bin/sh
# Host input priority, started through PolicyKit (pkexec) only when the
# desktop user cannot open the input devices. It reads this computer's
# keyboards and mice to notice that someone here uses them, and holds
# Sunshine's virtual keyboard and mouse while they do. It accepts nothing but
# a delay on stdin, writes no file and exits when stdin closes.
# See docs/host-input-priority.md.
set -eu
exec /usr/bin/python3 -I /usr/lib/big-remote-play/big_remote_play/host/input_priority.py run
