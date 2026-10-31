# Prints what the host runs as key=value lines. fleet-deployer sends this over
# SSH, and status-only hosts use it as the forced command for its key, so it
# must stay plain POSIX sh with no arguments.
printf 'current=%s\n' "$(readlink /run/current-system)"
printf 'boot=%s\n' "$(readlink -f /nix/var/nix/profiles/system)"
printf 'booted=%s\n' "$(readlink /run/booted-system)"
printf 'current_kernel=%s\n' "$(readlink /run/current-system/kernel)"
printf 'booted_kernel=%s\n' "$(readlink /run/booted-system/kernel)"
printf 'uptime=%s\n' "$(cut -d' ' -f1 /proc/uptime)"
