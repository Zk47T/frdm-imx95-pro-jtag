/*
 * physwin: physical memory through /dev/mem, for the JTAG shell on the i.MX95.
 *
 * The AHB-AP has 32-bit addresses (its CFG.LA reads 0), so the debugger sees only the low
 * 4 GB. The 2 GB trace buffer sits above that (0x4_0000_0000). This copies a piece of it
 * into a window below 4 GB that the host then reads over JTAG.
 *
 *   physwin r PHYS LEN          LEN bytes at PHYS to stdout
 *   physwin w PHYS LEN          up to LEN bytes from stdin to PHYS; prints the count
 *   physwin c SRC DST LEN       copy LEN bytes, SRC to DST
 *   physwin s PHYS LEN          a checksum (FNV-1a 64) of LEN bytes at PHYS, to compare with the host's
 *
 * Every mapping is O_SYNC (uncached): the ETR writes with MEMREMAP_WC semantics and the
 * AHB-AP reads behind the cores' caches, so nothing here may sit in a cache line.
 * Only for reserved (no-map) memory: STRICT_DEVMEM refuses System RAM anyway.
 */
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

#define CHUNK (1u << 20)

static int fd;

static volatile uint8_t *map(uint64_t phys, size_t len, uint64_t *base, size_t *maplen)
{
	uint64_t pg = phys & ~0xfffull;
	size_t ml = (size_t)(phys - pg) + len;
	void *p = mmap(NULL, ml, PROT_READ | PROT_WRITE, MAP_SHARED, fd, (off_t)pg);

	if (p == MAP_FAILED) {
		fprintf(stderr, "physwin: mmap 0x%llx+%zu: %s\n", (unsigned long long)pg, ml, strerror(errno));
		exit(1);
	}
	*base = pg;
	*maplen = ml;
	return (volatile uint8_t *)p + (phys - pg);
}

static void unmap(volatile uint8_t *p, uint64_t phys, uint64_t base, size_t ml)
{
	munmap((void *)(p - (phys - base)), ml);
}

/* word-wise when both ends are 8-aligned: device/uncached mappings dislike byte loops less */
static void copy(volatile uint8_t *d, const volatile uint8_t *s, size_t n)
{
	size_t i = 0;

	if (!(((uintptr_t)d | (uintptr_t)s) & 7))
		for (; i + 8 <= n; i += 8)
			*(volatile uint64_t *)(d + i) = *(const volatile uint64_t *)(s + i);
	for (; i < n; i++)
		d[i] = s[i];
}

int main(int argc, char **argv)
{
	if (argc < 4) {
		fprintf(stderr, "usage: physwin r|w|s PHYS LEN | physwin c SRC DST LEN\n");
		return 2;
	}
	fd = open("/dev/mem", O_RDWR | O_SYNC);
	if (fd < 0) {
		perror("/dev/mem");
		return 1;
	}
	char op = argv[1][0];
	uint64_t a = strtoull(argv[2], NULL, 0);
	uint64_t b = argc > 4 ? strtoull(argv[3], NULL, 0) : 0;
	uint64_t len = strtoull(argv[op == 'c' ? 4 : 3], NULL, 0);
	static uint8_t buf[CHUNK];
	uint64_t done = 0, h = 0xcbf29ce484222325ull;

	while (done < len) {
		size_t n = len - done > CHUNK ? CHUNK : (size_t)(len - done);
		uint64_t base, base2;
		size_t ml, ml2;
		volatile uint8_t *p = map(a + done, n, &base, &ml);

		if (op == 'r') {
			copy(buf, p, n);
			if (fwrite(buf, 1, n, stdout) != n)
				return 1;
		} else if (op == 'w') {
			size_t got = fread(buf, 1, n, stdin);

			copy(p, buf, got);
			if (got < n) {
				done += got;
				unmap(p, a + done - got, base, ml);
				break;
			}
		} else if (op == 'c') {
			volatile uint8_t *q = map(b + done, n, &base2, &ml2);

			copy(q, p, n);
			unmap(q, b + done, base2, ml2);
		} else if (op == 's') {
			copy(buf, p, n);
			for (size_t i = 0; i < n; i++)
				h = (h ^ buf[i]) * 0x100000001b3ull;
		}
		unmap(p, a + done, base, ml);
		done += n;
	}
	if (op == 'w')
		printf("%llu\n", (unsigned long long)done);
	if (op == 's')
		printf("fnv1a64 %016llx len %llu\n", (unsigned long long)h, (unsigned long long)done);
	return 0;
}
