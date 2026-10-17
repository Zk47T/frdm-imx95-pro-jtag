# Procedures for the i.MX95 over JTAG: the TAP's two IDCODEs, the DP's state, and the
# debug mailbox (ISP-AP, AP 0 behind SWJ-DP (AUTH)).
#
# The mailbox protocol is NXP SPSDK's (spsdk/dat/debug_mailbox.py, dm_commands.py), written
# again over 'dap apreg'. The script sends only commands that ask or start something
# (dm_allowed). Erase, provisioning, bricked mode and anything else that could change fuses
# or the life cycle is refused.
# Written 2026-10-15 from the documents; not yet run on the board.

# --- the TAP ---------------------------------------------------------------------------

proc imx95_swd {} { return [string match *swd* [transport select]] }

# The JTAGC's IDCODE (IR 0x01, the default after a TAP reset) and the JTAG-DP's (IR 0xFE).
# Over SWD there is no IR: the DP's DPIDR instead.
proc imx95_ids {} {
	if {[imx95_swd]} {
		echo "SW-DP DPIDR [imx95.dap dpreg 0x0] (ARM's DPs end in 477)"
		return
	}
	irscan imx95.cpu 0x01
	set jtagc 0x[drscan imx95.cpu 32 0]
	irscan imx95.cpu 0xfe
	set dp 0x[drscan imx95.cpu 32 0]
	irscan imx95.cpu 0xff
	echo [format "JTAGC IDCODE %s (bits 16:12 = 0x%02x, the RM says 0x18)" $jtagc [expr {($jtagc >> 12) & 0x1f}]]
	echo "JTAG-DP IDCODE $dp (ARM's DPs end in 477)"
}

# --- the DP ----------------------------------------------------------------------------

# Clear the DP's sticky errors, keeping both power-up requests: JTAG writes 1s to them in
# CTRL/STAT, SWD writes ABORT (STKERRCLR, WDERRCLR, ORUNERRCLR, STKCMPCLR).
proc dm_clear_sticky {} {
	if {[imx95_swd]} {
		catch {imx95.dap dpreg 0x0 0x1e}
	} else {
		catch {imx95.dap dpreg 0x4 0x50000032}
	}
}

proc dm_status {} {
	if {[catch {imx95.dap dpreg 0x4} cs]} {
		echo "DP CTRL/STAT unreadable: $cs"
		return
	}
	echo [format "DP CTRL/STAT 0x%08x: CDBGPWRUPACK %d, CSYSPWRUPACK %d, STICKYERR %d" \
		$cs [expr {($cs >> 29) & 1}] [expr {($cs >> 31) & 1}] [expr {($cs >> 5) & 1}]]
	foreach ap {0 1} {
		if {[catch {imx95.dap apreg $ap 0xfc} idr]} {
			echo "AP $ap IDR unreadable"
			continue
		}
		if {$idr == 0x002a0000} {
			set what "the debug mailbox: SWJ-DP (AUTH), debug not authenticated"
		} elseif {(($idr >> 13) & 0xf) == 0x8} {
			set what "a MEM-AP: the normal SWJ-DP"
		} else {
			set what ""
		}
		echo "AP $ap IDR $idr $what"
	}
}

# --- the debug mailbox -----------------------------------------------------------------

set dm_ap 0
set dm_timeout 2000
set DM_CSW 0x00
set DM_REQUEST 0x04
set DM_RETURN 0x08
set DM_IDR 0xfc
# CSW: RESYNCH_REQ 0x01, REQ_PENDING 0x02, DBG_OR_ERR 0x04, AHB_OR_ERR 0x08,
#      SOFT_RESET 0x10, CHIP_RESET_REQ 0x20 (resets the chip, not the mailbox)

# START, GET_CRP_LEVEL, EXIT, START_DBG_SESSION, DBG_AUTH_START
set dm_allowed {0x01 0x02 0x04 0x07 0x10}

proc dm_err {r} {
	switch -- [format 0x%08x $r] {
		0x00100001 { return "debug mode not entered: START (0x01) first" }
		0x00100002 { return "command not recognised in this life cycle" }
		0x00100003 { return "command failed" }
	}
	return "unknown status"
}

# Read a mailbox register, retrying. A read of RETURN stalls (JTAG WAIT) until the
# other side has written it.
proc dm_spin_rd {reg {ms 0}} {
	if {!$ms} { set ms $::dm_timeout }
	set t0 [clock milliseconds]
	while 1 {
		if {![catch {imx95.dap apreg $::dm_ap $reg} v]} { return [expr {$v}] }
		if {[clock milliseconds] - $t0 > $ms} { error "dm: reading AP reg $reg: $v" }
		dm_clear_sticky
		sleep 100
	}
}

# Write a mailbox register, then wait until the other side has taken it (REQ_PENDING clear).
proc dm_spin_wr {reg val} {
	set t0 [clock milliseconds]
	while {[catch {imx95.dap apreg $::dm_ap $reg $val} e]} {
		if {[clock milliseconds] - $t0 > $::dm_timeout} { error "dm: writing AP reg $reg: $e" }
		dm_clear_sticky
		sleep 100
	}
	while {[dm_spin_rd $::DM_CSW] & 0x2} {
		if {[clock milliseconds] - $t0 > $::dm_timeout} {
			error [format "dm: 0x%08x never taken (CSW REQ_PENDING)" $val]
		}
		sleep 10
	}
}

proc dm_idr {} {
	set idr [dm_spin_rd $::DM_IDR]
	echo [format "dm: IDR 0x%08x (SPSDK expects 0x002a0000)" $idr]
	return $idr
}

# Start talking to the mailbox. The other side (the ROM, or the ELE at run time) answers a
# resync by resetting the mailbox, so CSW then reads without REQ_PENDING.
# reset 1 also resets the chip: the board reboots, and the ROM serves the mailbox first.
proc dm_resync {{reset 0}} {
	set v [expr {$reset ? 0x21 : 0x01}]
	echo [format "dm: CSW <- 0x%02x (%s)" $v [expr {$reset ? "resync and chip reset" : "resync"}]]
	catch {imx95.dap apreg $::dm_ap $::DM_CSW $v}
	set t0 [clock milliseconds]
	set csw "unreadable"
	while 1 {
		if {![catch {imx95.dap apreg $::dm_ap $::DM_CSW} csw] && !($csw & 0x2)} {
			echo [format "dm: CSW %s after %d ms" $csw [expr {[clock milliseconds] - $t0}]]
			return $csw
		}
		if {[clock milliseconds] - $t0 > 5000} { error "dm: no answer to the resync in 5 s (CSW $csw)" }
		dm_clear_sticky
		sleep 50
	}
}

# One command, as SPSDK's DebugMailboxCommand.run():
#   REQUEST <- cmd | nparams << 16;
#   each parameter after an ack 0xA5A5 | remaining << 16 in RETURN;
#   then RETURN = status | length << 16, and length data words, each acked.
# resplen -1 takes the length the device announces.
# Returns the data words, or the status word when there are none.
proc dm_cmd {cmd {resplen 0} {params {}}} {
	if {[lsearch -exact $::dm_allowed [format 0x%02x $cmd]] < 0} {
		error [format "dm: command 0x%02x is not on this script's list" $cmd]
	}
	set n [llength $params]
	dm_spin_wr $::DM_REQUEST [expr {$cmd | ($n << 16)}]
	for {set i 0} {$i < $n} {incr i} {
		set r [dm_spin_rd $::DM_RETURN]
		if {($r & 0xffff) != 0xa5a5 || (($r >> 16) & 0xffff) != $n - $i} {
			error [format "dm: wanted the ack for parameter %d, got 0x%08x" $i $r]
		}
		dm_spin_wr $::DM_REQUEST [lindex $params $i]
	}
	set r [dm_spin_rd $::DM_RETURN]
	if {$resplen == 0 && ($r >> 20) == 0} { return $r }
	set len [expr {($r >> 16) & 0x7fff}]
	if {$r & 0xffff} {
		echo [format "dm: command 0x%02x -> 0x%08x: %s" $cmd $r [dm_err $r]]
		return $r
	}
	if {$resplen >= 0 && $len != $resplen} {
		error "dm: the device sends $len words, $resplen expected"
	}
	if {$len == 0} { return {} }
	dm_spin_wr $::DM_REQUEST [expr {0xa5a5 | ($len << 16)}]
	set words {}
	for {set i 0} {$i < $len} {incr i} {
		lappend words [dm_spin_rd $::DM_RETURN]
		dm_spin_wr $::DM_REQUEST [expr {0xa5a5 | (($len - $i - 1) << 16)}]
	}
	return $words
}

# EXIT. Parts with an ELE give no answer to it (SPSDK says so too).
proc dm_exit {} {
	dm_spin_wr $::DM_REQUEST 0x04
	if {[catch {dm_spin_rd $::DM_RETURN 500} r]} {
		echo "dm: EXIT sent, no answer"
	} else {
		echo [format "dm: EXIT -> 0x%08x" $r]
	}
}

# The test: is START_DBG_SESSION enough to open debug on this OEM-open part?
# Afterwards the port should reach the normal DP: AP 0 a MEM-AP, CSYSPWRUPACK set.
proc dm_session {{reset 0}} {
	dm_status
	dm_idr
	dm_resync $reset
	set r [dm_cmd 0x07]
	if {$r == 0x00100001} {
		echo [format "dm: START -> 0x%08x" [dm_cmd 0x01]]
		set r [dm_cmd 0x07]
	}
	if {$r} {
		echo [format "dm: START_DBG_SESSION -> 0x%08x (%s)" $r [dm_err $r]]
	} else {
		echo "dm: START_DBG_SESSION -> 0x00000000, success"
	}
	sleep 300
	# the DP behind the port may have changed: connect again
	if {[catch {dap init} e]} { echo "dap init: $e" }
	dm_status
}

# The challenge for a signed authentication (DAT). It only reads: nothing is answered.
# SPSDK turns the saved words into a DAC (DebugAuthenticationChallenge.parse) and, with
# the keys of the part, a DAR.
proc dm_dac {{reset 0}} {
	dm_resync $reset
	set w [dm_cmd 0x10 -1]
	if {[llength $w] < 2} {
		echo [format "dm: DBG_AUTH_START -> 0x%08x" $w]
		return
	}
	set name dac-[clock format [clock seconds] -format %Y%m%d-%H%M%S].txt
	set f [open $name w]
	foreach x $w { puts $f [format 0x%08x $x] }
	close $f
	echo "dm: DAC, [llength $w] words, in $name"
	foreach x $w { echo [format "  0x%08x" $x] }
}

# --- speed ---------------------------------------------------------------------------
# OpenOCD puts 255 idle clocks after every AHB-AP access, and the ch347 driver clocks
# them one by one in its slow bit mode: on the T113 that held memory to 16 KiB/s whatever
# the TCK, and 8 gave 107 KiB/s with the same data (2026-10-15). Call this once debug is
# open (AP 0 is the AHB-AP only then; before, it's the mailbox, which doesn't use them).
proc imx95_fast {} {
	set sel [imx95.dap apsel]
	imx95.dap apsel 0
	imx95.dap memaccess 8
	imx95.dap apsel $sel
}

# --- the MDM-AP (AP 6): what each core is doing, seen from outside ------------------------
# Readable only after authentication. RM 14.4: MDMSTATUS at 0x00, MDMSTATUS2 at 0x40,
# IDR 0x001C0030.

proc mdm_status {} {
	set s [imx95.dap apreg 6 0x00]
	set s2 [imx95.dap apreg 6 0x40]
	echo [format "MDMSTATUS 0x%08x: M33 halted %d, sleeping %d, deep %d; M7 halted %d, sleeping %d, deep %d; system reset %d" \
		$s [expr {($s >> 14) & 1}] [expr {($s >> 22) & 1}] [expr {($s >> 18) & 1}] \
		[expr {($s >> 13) & 1}] [expr {($s >> 21) & 1}] [expr {($s >> 17) & 1}] [expr {($s >> 2) & 1}]]
	echo [format "MDMSTATUS2 0x%08x: A55 cores (bit n = core n) in debug 0x%02x, in WFI 0x%02x, in WFE 0x%02x" \
		$s2 [expr {$s2 & 0x3f}] [expr {($s2 >> 8) & 0x3f}] [expr {($s2 >> 16) & 0x3f}]]
}

# Sample MDMSTATUS2 n times: the share of samples each A55 spends in WFI. An idle meter that
# doesn't touch the cores (on the T113 we found that the cycle counter stops in WFI, doc 78).
proc mdm_idle {{n 1000}} {
	set c {0 0 0 0 0 0}
	set t0 [clock milliseconds]
	for {set i 0} {$i < $n} {incr i} {
		set w [expr {([imx95.dap apreg 6 0x40] >> 8) & 0x3f}]
		for {set k 0} {$k < 6} {incr k} {
			if {$w & (1 << $k)} { lset c $k [expr {[lindex $c $k] + 1}] }
		}
	}
	echo "$n samples in [expr {[clock milliseconds] - $t0}] ms"
	for {set k 0} {$k < 6} {incr k} {
		echo [format "  core %d in WFI in %5.1f %% of the samples" $k [expr {100.0 * [lindex $c $k] / $n}]]
	}
}

# --- A55 PC sampling, without halting the core ----------------------------------------
# Our OpenOCD change 4 does this for the T113's A7 (DBGPCSR, 10k samples/s). On ARMv8 the
# sample register moved twice:
# - ARMv8.0: EDPCSR in the debug block, lo 0x0A0 (reading it takes the sample), hi 0x0AC;
#   EDDEVID (0xFC8) bits 3:0 nonzero when it is there.
# - ARMv8.2 (FEAT_PCSRv8p2): PMPCSR in the PMU block, lo 0x200, hi 0x204; PMDEVID (0xFC8)
#   bits 3:0 = 1. The A55 is a v8.2 core, so expect this one. Not checked on the chip.
# A sample of all ones means no sample: the core is halted, off, or non-invasive debug is
# not allowed.

proc a55_pcs_where {{core 0}} {
	set dbg [expr {$::_DBG0 + $core * 0x100000}]
	set pmu [expr {$dbg + 0x20000}]
	set ed [expr {[imx95.apb read_memory [expr {$dbg + 0xfc8}] 32 1] & 0xf}]
	set pm [expr {[imx95.apb read_memory [expr {$pmu + 0xfc8}] 32 1] & 0xf}]
	echo [format "core %d: EDDEVID.PCSample %d (debug block 0x%08x), PMDEVID.PCSample %d (PMU 0x%08x)" \
		$core $ed $dbg $pm $pmu]
	if {$pm} { return [list [expr {$pmu + 0x200}] [expr {$pmu + 0x204}]] }
	if {$ed} { return [list [expr {$dbg + 0x0a0}] [expr {$dbg + 0x0ac}]] }
	error "core $core: no PC sample register"
}

# The address in a sample: bits 55:0, with bit 55 extended (kernel addresses start 0xffff).
proc a55_pcs_addr {lo hi} {
	set v [expr {(($hi & 0x00ffffff) << 32) | ($lo & 0xffffffff)}]
	if {$v & (1 << 55)} { set v [expr {$v | (0xff << 56)}] }
	return $v
}

# n samples of core's PC; the 20 most frequent, and every sample in pcs-<time>.txt
# (one address a line, for addr2line -f -e vmlinux on the PC).
proc a55_pcs {{core 0} {n 2000}} {
	lassign [a55_pcs_where $core] lo hi
	set hist [dict create]
	set none 0
	set name pcs-[clock format [clock seconds] -format %Y%m%d-%H%M%S].txt
	set f [open $name w]
	set t0 [clock milliseconds]
	for {set i 0} {$i < $n} {incr i} {
		set l [imx95.apb read_memory $lo 32 1]
		set h [imx95.apb read_memory $hi 32 1]
		if {$l == 0xffffffff} { incr none; continue }
		set a [format 0x%016lx [a55_pcs_addr $l $h]]
		puts $f $a
		dict incr hist $a
	}
	close $f
	set ms [expr {[clock milliseconds] - $t0}]
	echo "$n samples in $ms ms, $none without a PC; all in $name"
	set rows {}
	dict for {a c} $hist { lappend rows [list $a $c] }
	foreach r [lrange [lsort -index 1 -integer -decreasing $rows] 0 19] {
		echo [format "  %s %6d" [lindex $r 0] [lindex $r 1]]
	}
}

# --- trace through the debugger: core n's ETM -> funnels -> ETF (FIFO) -> ETR -> DRAM -------
# (2026-10-16) Linux can't do this here: its first read of the ETF's PIDR (0x41030FE0, the A55's
# path) takes a synchronous external abort and kills init. The debugger's APB-AP reaches the same
# blocks at 0x8103xxxx (ETF), 0x8104xxxx (ETR), and core n's ETM at the debug base + 0x30000.
# The funnels have no registers (RM 14.1). The sequences are the kernel's own
# (coresight-etm4x-core.c etm4_enable_hw, coresight-tmc-etr.c __tmc_etr_enable_hw), except the
# exception-level filter: Linux runs at EL2 (VHE) here, so nothing is excluded.
# Needs IMX95_A55 (the imx95.apb target) and the core powered: boot with cpuidle.off=1.
set cs_etf 0x81030000
set cs_etr 0x81040000
set cs_lock 0xc5acce55

proc cs_r {a} { return [imx95.apb read_memory $a 32 1] }
proc cs_w {a v} { imx95.apb write_memory $a 32 [list $v] }
proc cs_wait {a mask val {ms 500}} {
	set t0 [clock milliseconds]
	while {([cs_r $a] & $mask) != $val} {
		if {[clock milliseconds] - $t0 > $ms} {
			error [format "cs: (%#x & %#x) never became %#x (now %#x)" $a $mask $val [cs_r $a]]
		}
	}
}
proc etm_base {core} { return [expr {$::_DBG0 + $core * 0x100000 + 0x30000}] }

# The ETM's ID registers, after the OS unlock; what an OpenCSD snapshot needs (etm_0.ini).
proc etm_ids {core} {
	set e [etm_base $core]
	cs_w [expr {$e + 0xfb0}] $::cs_lock
	cs_w [expr {$e + 0x300}] 0
	set out [format "TRCOSLSR=%#x TRCPDSR=%#x" [cs_r [expr {$e + 0x304}]] [cs_r [expr {$e + 0x314}]]]
	foreach {n o} {TRCIDR0 0x1e0 TRCIDR1 0x1e4 TRCIDR2 0x1e8 TRCIDR3 0x1ec TRCIDR4 0x1f0 TRCIDR5 0x1f4
	               TRCIDR8 0x180 TRCIDR9 0x184 TRCIDR10 0x188 TRCIDR11 0x18c TRCIDR12 0x190 TRCIDR13 0x194
	               TRCAUTHSTATUS 0xfb8 TRCDEVARCH 0xfbc} {
		append out [format " %s=%#x" $n [cs_r [expr {$e + $o}]]]
	}
	echo "etm $core: $out"
	return $out
}

# The sink: the ETR as a circular buffer at base (64-bit physical; its own AXI master, not behind
# the SMMU, no iommus in the DT) of size bytes, and the ETF as a hardware FIFO link in front of it.
proc cs_sink_start {base size} {
	set r $::cs_etr; set f $::cs_etf
	foreach b [list $r $f] { cs_w [expr {$b + 0xfb0}] $::cs_lock }
	cs_w [expr {$r + 0x20}] 0
	cs_wait [expr {$r + 0x0c}] 0x4 0x4
	cs_w [expr {$r + 0x04}] [expr {$size / 4}]
	cs_w [expr {$r + 0x28}] 0
	set ax [cs_r [expr {$r + 0x110}]]
	cs_w [expr {$r + 0x110}] [expr {($ax & ~0xfbf) | 0x2 | (0xf << 8)}]
	cs_w [expr {$r + 0x118}] [expr {$base & 0xffffffff}]
	cs_w [expr {$r + 0x11c}] [expr {$base >> 32}]
	cs_w [expr {$r + 0x14}] [expr {$base & 0xffffffff}]
	cs_w [expr {$r + 0x38}] [expr {$base >> 32}]
	cs_w [expr {$r + 0x18}] [expr {$base & 0xffffffff}]
	cs_w [expr {$r + 0x3c}] [expr {$base >> 32}]
	cs_w [expr {$r + 0x0c}] 0
	cs_w [expr {$r + 0x304}] 0x133
	cs_w [expr {$r + 0x34}] 0
	cs_w [expr {$r + 0x20}] 1
	cs_w [expr {$f + 0x20}] 0
	cs_wait [expr {$f + 0x0c}] 0x4 0x4
	cs_w [expr {$f + 0x28}] 2
	cs_w [expr {$f + 0x304}] 0x3
	cs_w [expr {$f + 0x34}] 0
	cs_w [expr {$f + 0x20}] 1
}

# What every ETM is programmed with (set before cs_multi_start / cs_trace_start):
#   cs_victlr     TRCVICTLR: 0x201 traces everything (Linux runs at EL2, VHE, so nothing is excluded by
#                 default); 0x100201 also excludes non-secure EL0: no user code, whose trace can't be
#                 decoded without the programs' images anyway, and which takes much of the bandwidth
#   cs_stallctlr  TRCSTALLCTLR: 0 never stalls; 0x10C (ISTALL, LEVEL 3) lets the ETM hold its core
#                 back when its FIFO fills, instead of overflowing (TRCIDR3.STALLCTL = 1 here)
if { ![info exists cs_victlr] } { set cs_victlr 0x201 }
if { ![info exists cs_stallctlr] } { set cs_stallctlr 0x0 }

# One core's ETM: OS unlock, keep powered, program, enable.
proc cs_etm_start {core traceid {configr 0x0} {ccctlr 0x0}} {
	set e [etm_base $core]
	cs_w [expr {$e + 0xfb0}] $::cs_lock
	cs_w [expr {$e + 0x300}] 0
	cs_w [expr {$e + 0x310}] [expr {[cs_r [expr {$e + 0x310}]] | 0x8}]
	cs_w [expr {$e + 0x004}] 0
	cs_wait [expr {$e + 0x00c}] 0x1 0x1
	cs_w [expr {$e + 0x010}] $configr
	cs_w [expr {$e + 0x018}] 0
	cs_w [expr {$e + 0x020}] 0
	cs_w [expr {$e + 0x024}] 0
	cs_w [expr {$e + 0x02c}] $::cs_stallctlr
	cs_w [expr {$e + 0x030}] 0
	cs_w [expr {$e + 0x034}] 0xc
	cs_w [expr {$e + 0x038}] $ccctlr
	cs_w [expr {$e + 0x03c}] 0
	cs_w [expr {$e + 0x040}] $traceid
	cs_w [expr {$e + 0x080}] $::cs_victlr
	cs_w [expr {$e + 0x084}] 0
	cs_w [expr {$e + 0x088}] 0
	cs_w [expr {$e + 0x004}] 1
	cs_wait [expr {$e + 0x00c}] 0x1 0x0
}

proc cs_etm_stop {core} {
	set e [etm_base $core]
	cs_w [expr {$e + 0x004}] 0
	cs_wait [expr {$e + 0x00c}] 0x1 0x1
}

# Stop the ETR on a flush (FFCR FlushMan|StopOnFl): the flush goes up through the ETF and the
# funnels to every ETM, and the ETR stops writing once it completes. That ends every core's trace
# at one instant. After it the ETR drops what still arrives without stalling the ATB: the ETMs ran
# on for 40 s, RWP didn't move, and each still reached idle (2026-10-16, T7). No-op if stopped.
proc cs_etr_flushstop {} {
	set r $::cs_etr
	if {[cs_r [expr {$r + 0x0c}]] & 0x4} { return }
	cs_w [expr {$r + 0x304}] [expr {[cs_r [expr {$r + 0x304}]] | 0x1040}]
	cs_wait [expr {$r + 0x304}] 0x40 0x0
	cs_wait [expr {$r + 0x0c}] 0x4 0x4
}

# Flush the ETF and the ETR (stop on flush), switch both off; returns the ETR's state. If the ETR
# has already stopped, the ETF's leftovers would be dropped anyway: it is only switched off.
proc cs_sink_stop {} {
	set r $::cs_etr; set f $::cs_etf
	if {!([cs_r [expr {$r + 0x0c}]] & 0x4)} {
		cs_w [expr {$f + 0x304}] [expr {[cs_r [expr {$f + 0x304}]] | 0x40}]
		cs_wait [expr {$f + 0x304}] 0x40 0x0
	}
	cs_w [expr {$f + 0x20}] 0
	cs_etr_flushstop
	set sts [cs_r [expr {$r + 0x0c}]]
	set rwp [expr {([cs_r [expr {$r + 0x3c}]] << 32) | [cs_r [expr {$r + 0x18}]]}]
	set rrp [expr {([cs_r [expr {$r + 0x38}]] << 32) | [cs_r [expr {$r + 0x14}]]}]
	cs_w [expr {$r + 0x20}] 0
	set s [format "STS=%#x full=%d memerr=%d RRP=%#x RWP=%#x" $sts [expr {$sts & 1}] [expr {($sts >> 5) & 1}] $rrp $rwp]
	echo "cs: stopped: $s"
	return $s
}

# One core (the original interface).
proc cs_trace_start {core base size {traceid 0x10} {configr 0x0} {ccctlr 0x0}} {
	cs_sink_start $base $size
	cs_etm_start $core $traceid $configr $ccctlr
	echo [format "cs: tracing core %d, trace id %#x, into %#x + %#x; ETR STS %#x" $core $traceid $base $size [cs_r [expr {$::cs_etr + 0x0c}]]]
}
proc cs_trace_stop {core} {
	return [cs_multi_stop [list $core]]
}

# Several cores into the same buffer: core n gets trace id 0x10 + n (the funnels merge them).
# The cores must be powered (cpuidle.off=1): an ETM access to a powered-off core wedges the APB-AP.
proc cs_multi_start {cores base size {configr 0x0} {ccctlr 0x0}} {
	cs_sink_start $base $size
	foreach c $cores { cs_etm_start $c [expr {0x10 + $c}] $configr $ccctlr }
	echo [format "cs: tracing cores {%s} (trace ids 0x10 + core) into %#x + %#x" $cores $base $size]
}
# The sink first, then the ETMs. Each cs_etm_stop takes ~1.8 ms over the CH347F: stopped first and
# one by one (T6), the cores still running went on filling the ring, and core 0's trace ended
# 7.7 ms before core 4's. With the ETR stopped first, every core ends at the same timestamp (T7).
# (The start is still one core after another, ~ms apart; a ring that has wrapped doesn't show it.)
proc cs_multi_stop {cores} {
	cs_etr_flushstop
	foreach c $cores { cs_etm_stop $c }
	return [cs_sink_stop]
}
