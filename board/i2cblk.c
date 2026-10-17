/* i2cblk: probe an i2c bus and do SMBus block reads, via the i2c-dev ioctl interface. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/ioctl.h>
#include <linux/types.h>

#define I2C_SLAVE        0x0703
#define I2C_SMBUS        0x0720
#define I2C_SMBUS_READ   1
#define I2C_SMBUS_BYTE   1
#define I2C_SMBUS_BLOCK_DATA 5
#define I2C_SMBUS_BLOCK_MAX 32

union i2c_smbus_data { __u8 byte; __u16 word; __u8 block[34]; };
struct i2c_smbus_ioctl_data { __u8 read_write; __u8 command; __u32 size; union i2c_smbus_data *data; };

static int xfer(int fd, __u8 rw, __u8 cmd, __u32 size, union i2c_smbus_data *d) {
    struct i2c_smbus_ioctl_data a = { rw, cmd, size, d };
    return ioctl(fd, I2C_SMBUS, &a);
}

int main(int argc, char **argv) {
    if (argc < 3) { fprintf(stderr, "usage: i2cblk /dev/i2c-N scan | /dev/i2c-N 0xADDR block 0xCMD [loops]\n"); return 2; }
    int fd = open(argv[1], O_RDWR);
    if (fd < 0) { perror("open"); return 1; }
    if (!strcmp(argv[2], "scan")) {
        for (int a = 0x08; a < 0x78; a++) {
            if (ioctl(fd, I2C_SLAVE, a) < 0) continue;
            union i2c_smbus_data d;
            if (xfer(fd, I2C_SMBUS_READ, 0, I2C_SMBUS_BYTE, &d) >= 0)
                printf("ACK 0x%02x (byte 0x%02x)\n", a, d.byte);
        }
        return 0;
    }
    int addr = strtol(argv[2], 0, 0);
    __u8 cmd = (argc > 4) ? strtol(argv[4], 0, 0) : 0;
    long loops = (argc > 5) ? strtol(argv[5], 0, 0) : 1;
    if (ioctl(fd, I2C_SLAVE, addr) < 0) { perror("I2C_SLAVE"); return 1; }
    long ok = 0, err = 0;
    for (long i = 0; i < loops; i++) {
        union i2c_smbus_data d; memset(&d, 0, sizeof d);
        int r = xfer(fd, I2C_SMBUS_READ, cmd, I2C_SMBUS_BLOCK_DATA, &d);
        if (r < 0) { err++; if (i == 0) perror("smbus block read"); }
        else { ok++; if (loops == 1) { printf("len %d:", d.block[0]); for (int j=1;j<=d.block[0]&&j<=32;j++) printf(" %02x", d.block[j]); printf("\n"); } }
        usleep(2000);
    }
    if (loops > 1) printf("block reads: %ld ok, %ld err\n", ok, err);
    return 0;
}
