#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include "stats.h"
int main(void)
{
    struct stray_va_stats r;
    unsigned reports = 0;
    for (unsigned i = 1; i <= 1024; ++i)
    {
        int hint = i % 4 != 1, hit = hint && i % 4 != 0;
        r.peb = 0;
        stray_va_account(&r, 0x11, 5, hint, hit, i == 4 ? 64 : 63, hint ? 213 : 0, hit ? 0 : 107);
        if (r.peb)
        {
            ++reports;
            assert(i == 1 || i == 4 || i == 128 || i == 256 || i == 512 || i == 1024);
            assert(r.peb == 0x11 && r.pid == 5 && r.hits + r.full == i);
        }
        if (i == 1)
        {
            struct stray_va_stats other = {0};
            stray_va_account(&other, 0x22, 8, 0, 0, 64, 0, 314);
            assert(other.full == 1 && other.expensive == 1 && !other.misses && other.full_ns == 314);
        }
    }
    assert(reports == 6 && r.full == 512 && r.expensive == 1 && r.hits == 512 && r.misses == 256);
    assert(r.hint_ns == UINT64_C(768)*213 && r.full_ns == UINT64_C(512)*107);
    puts("PASS two guest keys, hint/fallback, 63/64 rejections, cumulative ns, sparse snapshots");
}
