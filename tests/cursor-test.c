#include <assert.h>
#include <inttypes.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "cursor.h"

static size_t search(const unsigned char *map, size_t lo, size_t hi, size_t width, uint64_t *probes)
{
    for (size_t i = lo; i < hi && width <= hi - i; ++i)
    {
        ++*probes;
        size_t j = 0;
        while (j < width && !map[i+j]) ++j;
        if (j == width) return i;
    }
    return 0;
}

static size_t allocate(const unsigned char *map, size_t hi, size_t width, struct stray_va_cursor *c,
                       uint64_t *probes, int *did_hit)
{
    uintptr_t f, e;
    size_t placed = 0;
    uint64_t before_full;
    if (stray_va_hint(c, 1, 2, 1, hi, width, 0, &f, &e))
        placed = search(map, f, e, width, probes);
    *did_hit = !!placed;
    before_full = *probes;
    if (!placed) placed = search(map, 1, hi, width, probes);
    if (placed) stray_va_result(c, 1, 2, 1, hi, placed, width, *did_hit,
                                (unsigned int)(*probes - before_full - !*did_hit));
    return placed;
}

int main(void)
{
    unsigned char model[12] = {0};
    size_t checks = 0;
    for (unsigned mask = 0; mask < 1024; ++mask)
    {
        for (unsigned j = 1; j <= 10; ++j) model[j] = (mask >> (j-1)) & 1;
        for (size_t w=1; w<=10; ++w)
        for (size_t next=1; next<=11; ++next)
        {
            uint64_t work = 0;
            struct stray_va_cursor c = {1, 2, 1, 11, next, 64};
            size_t orig = search(model, 1, 11, w, &work);
            int hit = 0;
            size_t r = allocate(model, 11, w, &c, &work, &hit);
            assert(!!r == !!orig && (hit || orig == r));
            if (r)
            {
                assert(w <= 11-r);
                for (size_t j=0; j<w; ++j) assert(!model[r+j]);
            }
            ++checks;
        }
    }
    {
        struct stray_va_cursor c = {1, 2, 0x10000, 0x1000000, 0x10001, 64};
        uintptr_t first, end;
        assert(stray_va_hint(&c, 1, 2, c.lo, c.hi, 0x10000, 0xffff, &first, &end));
        assert(first == 0x20000 && end == first + 16*0x10000);
        assert(!stray_va_hint(&c, 2, 2, c.lo, c.hi, 1, 0, &first, &end));
        assert(!stray_va_hint(&c, 1, 3, c.lo, c.hi, 1, 0, &first, &end));
        assert(!stray_va_hint(&c, 1, 2, c.lo+1, c.hi, 1, 0, &first, &end));
        c.next = UINTPTR_MAX - 2; c.hi = UINTPTR_MAX;
        assert(!stray_va_hint(&c, 1, 2, c.lo, c.hi, 1, 15, &first, &end));
        c.next = UINTPTR_MAX-16;
        assert(stray_va_hint(&c, 1, 2, c.lo, c.hi, 16, 0, &first, &end));
        assert(end == UINTPTR_MAX);
    }
    {
        unsigned char plain[6400]={0}, hinted[6400]={0};
        struct stray_va_cursor c={0};
        uint64_t a=0, b=0;
        unsigned hits=0;
        memset(plain, 1, 4097);
        memcpy(hinted, plain, sizeof(hinted));
        for (unsigned i=0; i<1024; ++i)
        {
            int hit=0;
            size_t x=search(plain, 1, sizeof(plain), 1, &a);
            size_t y=allocate(hinted, sizeof(hinted), 1, &c, &b, &hit);
            assert(x && y && !plain[x] && !hinted[y]);
            plain[x]=hinted[y]=1;
            hits+=hit;
        }
        assert(hits < 1024 && hits > 990 && b*30 < a);
        printf("%zu exhaustive comparisons; 1024 allocations after 4096 occupied slots: "
               "%" PRIu64 " baseline probes, %" PRIu64 " candidate probes (%u hints hit).\n",
               checks, a, b, hits);
    }
    return 0;
}
