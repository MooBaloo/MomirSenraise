#include <jni.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdint.h>
#include <sys/file.h>
#include <sys/ioctl.h>
#include <termios.h>
#include <time.h>
#include <unistd.h>

static int64_t now_ms(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) != 0) return -1;
    return (int64_t) now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

/* Bound retries, including EINTR. Never accept an infinite Java timeout. */
static int wait_ready(int fd, short events, int64_t deadline) {
    for (;;) {
        const int64_t now = now_ms();
        if (now < 0) return -EIO;
        if (now >= deadline) return 0;
        struct pollfd p = {.fd = fd, .events = events, .revents = 0};
        const int result = poll(&p, 1, (int) (deadline - now));
        if (result < 0) {
            if (errno == EINTR) continue;
            return -errno;
        }
        if (result == 0) return 0;
        if (p.revents & (POLLERR | POLLHUP | POLLNVAL)) return -EIO;
        if (p.revents & events) return 1;
    }
}

JNIEXPORT jint JNICALL Java_software_zeasy_momir_print_NativeSerial_open(JNIEnv *env, jobject self) {
    (void) env; (void) self;
    const int fd = open("/dev/ttyS1", O_RDWR | O_NOCTTY | O_NONBLOCK | O_CLOEXEC);
    if (fd < 0) return -errno;
    if (flock(fd, LOCK_EX | LOCK_NB) != 0) goto failure;
    struct termios config;
    if (tcgetattr(fd, &config) != 0) goto failure;
    cfmakeraw(&config);
    config.c_cflag &= ~(CSIZE | PARENB | PARODD | CSTOPB);
    config.c_cflag |= CS8 | CRTSCTS | CLOCAL | CREAD;
    config.c_iflag &= ~(IXON | IXOFF | IXANY);
    config.c_cc[VMIN] = 0;
    config.c_cc[VTIME] = 0;
    if (cfsetispeed(&config, B460800) != 0 || cfsetospeed(&config, B460800) != 0 ||
        tcsetattr(fd, TCSANOW, &config) != 0 || tcflush(fd, TCIOFLUSH) != 0) goto failure;
    return fd;
failure: {
    const int saved = errno;
    close(fd);
    return -saved;
}
}

JNIEXPORT jint JNICALL Java_software_zeasy_momir_print_NativeSerial_close(JNIEnv *env, jobject self, jint fd) {
    (void) env; (void) self;
    if (fd < 0) return -EINVAL;
    /* Never retry close on EINTR: the descriptor may already have been consumed. */
    return close(fd) == 0 ? 0 : -errno;
}

static jint transfer(JNIEnv *env, jint fd, jbyteArray array, jint offset, jint length,
                     jint timeout_ms, int writing) {
    if (fd < 0 || array == NULL || offset < 0 || length < 0 || timeout_ms < 1 || timeout_ms > 5000)
        return -EINVAL;
    const jsize capacity = (*env)->GetArrayLength(env, array);
    if (length > capacity || offset > capacity - length) return -EINVAL;
    if (length == 0) return 0;
    const int64_t start = now_ms();
    if (start < 0) return -EIO;
    const int64_t deadline = start + timeout_ms;
    for (;;) {
        const int ready = wait_ready(fd, writing ? POLLOUT : POLLIN, deadline);
        if (ready <= 0) return ready;
        jbyte *bytes = (*env)->GetByteArrayElements(env, array, NULL);
        if (bytes == NULL) return -ENOMEM;
        const ssize_t count = writing ? write(fd, bytes + offset, (size_t) length)
                                      : read(fd, bytes + offset, (size_t) length);
        const int saved = errno;
        (*env)->ReleaseByteArrayElements(env, array, bytes, writing ? JNI_ABORT : 0);
        if (count < 0 && (saved == EINTR || saved == EAGAIN || saved == EWOULDBLOCK)) continue;
        return count < 0 ? -saved : (jint) count;
    }
}

JNIEXPORT jint JNICALL Java_software_zeasy_momir_print_NativeSerial_read(
        JNIEnv *env, jobject self, jint fd, jbyteArray bytes, jint offset, jint length, jint timeout_ms) {
    (void) self;
    return transfer(env, fd, bytes, offset, length, timeout_ms, 0);
}

JNIEXPORT jint JNICALL Java_software_zeasy_momir_print_NativeSerial_write(
        JNIEnv *env, jobject self, jint fd, jbyteArray bytes, jint offset, jint length, jint timeout_ms) {
    (void) self;
    return transfer(env, fd, bytes, offset, length, timeout_ms, 1);
}

JNIEXPORT jint JNICALL Java_software_zeasy_momir_print_NativeSerial_drain(
        JNIEnv *env, jobject self, jint fd, jint timeout_ms) {
    (void) env; (void) self;
    if (fd < 0 || timeout_ms < 1 || timeout_ms > 5000) return -EINVAL;
    const int64_t start = now_ms();
    if (start < 0) return -EIO;
    const int64_t deadline = start + timeout_ms;
    for (;;) {
        const int64_t now = now_ms();
        if (now < 0) return -EIO;
        if (now >= deadline) return -ETIMEDOUT;
        int queued = 0;
        if (ioctl(fd, TIOCOUTQ, &queued) != 0) {
            if (errno == EINTR) continue;
            return -errno;
        }
        if (queued == 0) return 0;
        const struct timespec pause = {.tv_sec = 0, .tv_nsec = 10000000};
        nanosleep(&pause, NULL);
    }
}
