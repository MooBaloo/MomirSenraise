#include <assert.h>
#include <stdarg.h>
#include <string.h>
#include <sys/ioctl.h>
#include <jni.h>
static int drain_queue;
static int fake_ioctl(int fd, unsigned long request, ...) {
    (void) fd;
    assert(request == TIOCOUTQ);
    va_list args; va_start(args, request);
    *va_arg(args, int *) = drain_queue;
    va_end(args);
    return 0;
}
#define ioctl fake_ioctl
#include "../../app/src/main/jni/momir_serial.c"
#undef ioctl

struct array { jsize length; jbyte bytes[8]; };
static jsize array_length(JNIEnv *env, jarray a) { (void) env; return ((struct array *) a)->length; }
static jbyte *array_bytes(JNIEnv *env, jbyteArray a, jboolean *copy) {
    (void) env; (void) copy; return ((struct array *) a)->bytes;
}
static void release_bytes(JNIEnv *env, jbyteArray a, jbyte *bytes, jint mode) {
    (void) env; (void) a; (void) bytes; (void) mode;
}
int main(void) {
    const __typeof__(**(JNIEnv *)0) api = {
        .GetArrayLength = array_length, .GetByteArrayElements = array_bytes,
        .ReleaseByteArrayElements = release_bytes,
    };
    JNIEnv env = &api;
    struct array a = {.length = 8, .bytes = {1,2,3,4,5,6,7,8}};
    int pair[2]; assert(pipe(pair) == 0);
    assert(fcntl(pair[0], F_SETFL, O_NONBLOCK) == 0);
    assert(fcntl(pair[1], F_SETFL, O_NONBLOCK) == 0);
    assert(transfer(&env, pair[1], NULL, 0, 1, 10, 1) == -EINVAL);
    assert(transfer(&env, pair[1], (jbyteArray) &a, 7, 2, 10, 1) == -EINVAL);
    assert(transfer(&env, pair[1], (jbyteArray) &a, 0, INT32_MAX, 10, 1) == -EINVAL);
    assert(transfer(&env, pair[1], (jbyteArray) &a, -1, 1, 10, 1) == -EINVAL);
    assert(transfer(&env, pair[1], (jbyteArray) &a, 0, 1, -1, 1) == -EINVAL);
    assert(transfer(&env, pair[1], (jbyteArray) &a, 0, 1, 5001, 1) == -EINVAL);
    assert(transfer(&env, pair[0], (jbyteArray) &a, 0, 1, 10, 0) == 0);
    assert(transfer(&env, pair[1], (jbyteArray) &a, 2, 3, 10, 1) == 3);
    memset(a.bytes, 0, sizeof(a.bytes));
    assert(transfer(&env, pair[0], (jbyteArray) &a, 1, 3, 10, 0) == 3);
    assert(a.bytes[0] == 0 && a.bytes[1] == 3 && a.bytes[2] == 4 && a.bytes[3] == 5 && a.bytes[4] == 0);
    drain_queue = 1;
    const int64_t start = now_ms();
    assert(Java_software_zeasy_momir_print_NativeSerial_drain(&env, NULL, pair[1], 20) == -ETIMEDOUT);
    assert(now_ms() - start < 1000);
    drain_queue = 0;
    assert(Java_software_zeasy_momir_print_NativeSerial_drain(&env, NULL, pair[1], 20) == 0);
    assert(Java_software_zeasy_momir_print_NativeSerial_drain(&env, NULL, pair[1], -1) == -EINVAL);
    close(pair[1]);
    assert(transfer(&env, pair[0], (jbyteArray) &a, 0, 1, 10, 0) == -EIO);
    close(pair[0]);
    return 0;
}
