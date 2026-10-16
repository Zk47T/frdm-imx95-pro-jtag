# Sourced by hand in the JTAG shell ('. ./jtag-env.sh'): short names for the paths that only
# work relative to the shell's tmpfs (the shell's own root is the emptied initramfs).
R=proc/1/root                    # the real root filesystem (eMMC)
CS=sys/bus/coresight/devices     # the CoreSight devices
alias ll='ls -l'
# run a program of the real root in its own root: rr /usr/bin/whatever args
rr() { ./busybox chroot $R "$@"; }
