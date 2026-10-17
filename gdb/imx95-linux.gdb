# gdb helpers for the FRDM-IMX95-PRO's Linux through OpenOCD (gdb port 3333, all six A55s as threads):
# which task runs on which core, and who writes a variable.
#   gdb-multiarch vmlinux -ex 'target extended-remote :3333' -x gdb/imx95-linux.gdb
#   (gdb) tasks                     the task on each A55
#   (gdb) current [CORE]            the task on one core (default: the current gdb thread's)
#   (gdb) whowrote vm_dirty_ratio   a hardware watchpoint; when it fires: backtrace + the writing task
#   (gdb) hbreak sdhci_irq          kernel text is read-only: hardware breakpoints (6 per core)
# Every stop halts all six cores (one SMP group): keep stops short, RCU warns after about 21 s.
# 'detach' resumes them (openocd/target/imx95.cfg).
#
# The current task: arm64 keeps it per CPU in __entry_task (set in __switch_to). This kernel's debug
# info has no struct task_struct (CONFIG_DEBUG_INFO_REDUCED), so two offsets are this build's
# (linux-imx 6.18.20, 2026-10-17):
#   pid  = TSK_STACK_CANARY - 8 = 1176 (include/generated/asm-offsets.h: pid, tgid, stack_canary)
#   comm = 1664, where init_task holds "swapper/0" (find /b &init_task, +4096, 's', 'w', ...)
# Check them after a kernel rebuild with 'tasks': an idle core shows pid 0, swapper/N.
set $pid_off = 1176
set $comm_off = 1664

define current
  if $argc == 1
    set $c = $arg0
  else
    set $c = $_thread - 1
  end
  set $t = *(long*)((char*)&__entry_task + __per_cpu_offset[$c])
  printf "core %d: pid %d (%s), task_struct %#lx\n", $c, *(int*)($t + $pid_off), (char*)($t + $comm_off), $t
end
document current
The task running on an A55 (default: the core of the current gdb thread): pid, name, task_struct.
end

define tasks
  set $c = 0
  while $c < 6
    current $c
    set $c = $c + 1
  end
end
document tasks
The task on each of the six A55s.
end

define whowrote
  watch $arg0
  continue
  bt 16
  current
  delete
end
document whowrote
whowrote SYMBOL: a hardware watchpoint on SYMBOL; when something writes it, the backtrace and the task.
end
