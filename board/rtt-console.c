/*
 * rtt-console: a shell for the debugger, through memory. No USB, no UART: the
 * host reaches it over JTAG (the AHB-AP) with OpenOCD's RTT server.
 *
 * It puts a SEGGER RTT control block with one up and one down buffer in DRAM
 * that Linux doesn't use (mem=240M leaves the top 16 MB to us), mapped
 * uncached through /dev/mem, so the AHB-AP and this program see the same bytes
 * without cache maintenance. A shell runs on a pty: what it prints goes into
 * the up buffer, what the host writes into the down buffer goes to it.
 *
 *   rtt-console [PHYS [SHELL]]   default 0x4FC00000 (64 KiB), /bin/sh
 * The shell starts in the directory this program was started from, with HOME there and no
 * history file: on an NFS root whose link is down, a history write or any absolute path
 * (the walk checks permission on "/", an NFS RPC) hangs the shell (doc 80 §13). So, run
 * from a tmpfs (/run), it also mounts proc, sysfs, configfs and devtmpfs under ./proc,
 * ./sys, ./cfg and ./dev, and uses ./busybox (a static one) as the shell if it's there:
 * then relative paths reach the kernel without touching the root filesystem, e.g.
 *   echo musb-hdrc.2.auto > cfg/usb_gadget/elb1/UDC     cat proc/uptime     dmesg
 * Host: ../jtag-console.sh (OpenOCD 'rtt setup PHYS 0x100 "SEGGER RTT"' + a TCP port).
 *
 * The block holds physical addresses: OpenOCD reads it and the buffers through
 * the AHB-AP, which sees physical memory. The ID string is written last, so the
 * host never finds a half-built block.
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <termios.h>
#include <unistd.h>

#define MAP_SIZE	0x10000
#define OFF_CB		0x0000		/* the control block, 72 bytes */
#define OFF_NAMES	0x0100
#define OFF_UP		0x1000		/* target -> host */
#define UP_SIZE		0x8000
#define OFF_DOWN	0x9000		/* host -> target */
#define DOWN_SIZE	0x1000

struct rtt_ring {			/* SEGGER_RTT_BUFFER_UP/DOWN, 32-bit */
	uint32_t name;
	uint32_t buf;
	uint32_t size;
	uint32_t wr;
	uint32_t rd;
	uint32_t flags;
};

struct rtt_cb {
	char id[16];
	int32_t max_up;
	int32_t max_down;
	struct rtt_ring up;
	struct rtt_ring down;
};

static volatile uint8_t *mem;

static void barrier(void)
{
	__sync_synchronize();
}

/* copy n bytes from src into the up ring as far as there is room; returns bytes taken */
static size_t up_put(volatile struct rtt_ring *r, const uint8_t *src, size_t n)
{
	uint32_t wr = r->wr, rd = r->rd, size = r->size;
	size_t done = 0;

	while (done < n) {
		uint32_t next = wr + 1 == size ? 0 : wr + 1;

		if (next == rd)
			break;			/* full: the host hasn't read yet */
		mem[OFF_UP + wr] = src[done++];
		wr = next;
	}
	barrier();
	r->wr = wr;
	return done;
}

/* take what the host wrote into the down ring */
static size_t down_get(volatile struct rtt_ring *r, uint8_t *dst, size_t max)
{
	uint32_t wr = r->wr, rd = r->rd, size = r->size;
	size_t done = 0;

	barrier();
	while (rd != wr && done < max) {
		dst[done++] = mem[OFF_DOWN + rd];
		rd = rd + 1 == size ? 0 : rd + 1;
	}
	barrier();
	r->rd = rd;
	return done;
}

static const char *shell = "/bin/sh";

static pid_t start_shell(int *master)
{
	int m = posix_openpt(O_RDWR | O_NOCTTY);

	if (m < 0 || grantpt(m) || unlockpt(m))
		return -1;
	pid_t pid = fork();

	if (pid == 0) {
		setsid();
		int s = open(ptsname(m), O_RDWR);

		if (s < 0)
			_exit(1);
		ioctl(s, TIOCSCTTY, 0);
		dup2(s, 0);
		dup2(s, 1);
		dup2(s, 2);
		close(m);
		if (s > 2)
			close(s);
		/* a clean environment: nothing inherited may send the shell or its
		 * applets to an absolute path (LANG -> locale files, PATH -> /bin) */
		const char *home = getenv("HOME");
		char h[256];

		snprintf(h, sizeof(h), "%s", home ? home : "/");
		clearenv();
		setenv("HOME", h, 1);
		setenv("PATH", ".", 1);
		setenv("TERM", "vt100", 1);
		setenv("PS1", "jtag# ", 1);
		execl(shell, "sh", "-i", (char *)NULL);
		_exit(127);
	}
	*master = m;
	return pid;
}

int main(int argc, char **argv)
{
	unsigned long phys = argc > 1 ? strtoul(argv[1], NULL, 0) : 0x4FC00000ul;

	if (argc > 2)
		shell = argv[2];
	/* stay off the root filesystem: HOME and the working directory where we are */
	char here[256];

	if (getcwd(here, sizeof(here))) {
		setenv("HOME", here, 1);
		if (strcmp(here, "/")) {
			static const char *const fs[][2] = {
				{ "proc", "proc" }, { "sys", "sysfs" }, { "cfg", "configfs" }, { "dev", "devtmpfs" },
			};

			for (unsigned i = 0; i < sizeof(fs) / sizeof(fs[0]); i++) {
				mkdir(fs[i][0], 0755);
				if (mount(fs[i][1], fs[i][0], fs[i][1], 0, "") && errno != EBUSY)
					fprintf(stderr, "rtt-console: mount %s: %s\n", fs[i][0], strerror(errno));
			}
		}
	}
	if (argc <= 2 && !access("busybox", X_OK))
		shell = "./busybox";
	/* i.MX95: the devtmpfs mounted above first. Started in the background by an
	 * initramfs /init that moves /dev away right after, "/dev/mem" would be a race. */
	int fd = open("dev/mem", O_RDWR | O_SYNC);

	if (fd < 0)
		fd = open("/dev/mem", O_RDWR | O_SYNC);

	if (fd < 0) {
		perror("/dev/mem");
		return 1;
	}
	mem = mmap(NULL, MAP_SIZE, PROT_READ | PROT_WRITE, MAP_SHARED, fd, phys);
	if (mem == MAP_FAILED) {
		perror("mmap");
		return 1;
	}

	volatile struct rtt_cb *cb = (volatile struct rtt_cb *)(mem + OFF_CB);
	/* no ID while the block is being built */
	for (int i = 0; i < 16; i++)
		cb->id[i] = 0;
	barrier();
	strcpy((char *)(mem + OFF_NAMES), "Terminal");
	cb->max_up = 1;
	cb->max_down = 1;
	cb->up = (struct rtt_ring){ phys + OFF_NAMES, phys + OFF_UP, UP_SIZE, 0, 0, 0 };
	cb->down = (struct rtt_ring){ phys + OFF_NAMES, phys + OFF_DOWN, DOWN_SIZE, 0, 0, 0 };
	barrier();
	memcpy((void *)cb->id, "SEGGER RTT", 10);
	barrier();
	fprintf(stderr, "rtt-console: control block at 0x%08lx; host: jtag-console.sh\n", phys);

	/* a minimal initramfs may have /dev/ptmx but no devpts mounted */
	if (access("/dev/pts/ptmx", F_OK)) {
		mkdir("/dev/pts", 0755);
		if (mount("devpts", "/dev/pts", "devpts", 0, "") && errno != EBUSY)
			perror("mount /dev/pts");
	}
	signal(SIGPIPE, SIG_IGN);
	for (;;) {
		int master;
		pid_t pid = start_shell(&master);

		if (pid < 0) {
			perror("pty");
			return 1;
		}
		uint8_t buf[512];
		size_t pend = 0, poff = 0;

		for (;;) {
			struct pollfd p = { master, POLLIN, 0 };

			if (pend == 0 && poll(&p, 1, 5) > 0) {
				ssize_t n = read(master, buf, sizeof(buf));

				if (n <= 0)
					break;		/* the shell exited */
				pend = n;
				poff = 0;
			}
			if (pend) {
				size_t t = up_put(&cb->up, buf + poff, pend);

				poff += t;
				pend -= t;
				if (!t)
					usleep(2000);	/* up ring full: wait for the host */
			}
			uint8_t in[256];
			size_t k = down_get(&cb->down, in, sizeof(in));

			if (k && write(master, in, k) < 0)
				break;
		}
		close(master);
		waitpid(pid, NULL, 0);
		static const char msg[] = "\r\n[rtt-console: shell exited, starting a new one]\r\n";

		up_put(&cb->up, (const uint8_t *)msg, sizeof(msg) - 1);
	}
}
