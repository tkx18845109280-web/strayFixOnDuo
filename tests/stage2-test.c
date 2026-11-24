#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "cursor.h"
#include "stage2.h"

enum { LO = 8, LIMIT = 1024 };
enum { FULL, FIRST, SECOND };
struct outcome { uintptr_t address; unsigned path, first, second, full; };

static uintptr_t scan(const unsigned char *map, uintptr_t start, uintptr_t end,
                      uintptr_t size, uintptr_t mask, unsigned *probes)
{
    for (uintptr_t a = (start + mask) & ~mask; a < end && size <= end - a; a += mask + 1)
    {
        ++*probes;
        uintptr_t b = 0;
        while (b < size && !map[a + b]) ++b;
        if (b == size) return a;
    }
    return 0;
}

static struct outcome map_one(const unsigned char *map, struct stray_va_cursor *cursor,
                              uintptr_t hi, uintptr_t size, uintptr_t mask, int stage2, int publish)
{
    uintptr_t first, end, next;
    struct stray_va_cursor proposed = *cursor;
    struct outcome result = {0};
    if (stray_va_hint(&proposed, 1, 2, LO, hi, size, mask, &first, &end))
    {
        result.address = scan(map, first, end, size, mask, &result.first);
        if (result.address) result.path = FIRST;
        else if (stage2 && stray_va_second_hint(first, hi, size, mask, &next, &end))
        {
            result.address = scan(map, next, end, size, mask, &result.second);
            if (result.address) result.path = SECOND;
        }
    }
    if (!result.address) result.address = scan(map, LO, hi, size, mask, &result.full);
    if (result.address)
    {
        stray_va_result(&proposed, 1, 2, LO, hi, result.address, size, result.path != FULL,
                        result.full ? result.full - 1 : 0);
        if (publish) *cursor = proposed;
    }
    return result;
}

static void geometry(void)
{
    uint64_t checked = 0;
    for (uintptr_t step = 1; step <= 64; step *= 2)
    for (unsigned count = 0; count <= 300; ++count)
    for (uintptr_t size = 1; size <= step * 3 + 1; size += step)
    {
        uintptr_t start = 2 * step;
        uintptr_t hi = start + count * step + size;
        struct stray_va_cursor c = {1, 2, step, hi, start, 64};
        uintptr_t first, a, next = 123, b = 456;
        assert(stray_va_hint(&c, 1, 2, step, hi, size, step - 1, &first, &a));
        assert(first == start);
        assert((a - size - first) / step + 1 == (count < 15 ? count + 1 : 16));
        int second = stray_va_second_hint(first, hi, size, step - 1, &next, &b);
        assert(second == (count >= 16));
        if (second)
        {
            assert(next == a - size + step);
            assert(next == start + 16 * step);
            assert(((b - size) - next) / step + 1 == (count < 255 ? count - 15 : 240));
            assert(b <= hi && b >= next + size);
        }
        else assert(next == 123 && b == 456);
        ++checked;
    }
    uintptr_t m = UINTPTR_MAX, start = m - 320, first = 3, end = 5;
    assert(stray_va_second_hint(start, m, 65, 0, &first, &end));
    assert(first == start + 16 && end == m);
    assert(!stray_va_second_hint(m - 15, m, 1, 0, &first, &end));
    assert(!stray_va_second_hint(m - 15, m, 16, 0, &first, &end));
    assert(!stray_va_second_hint(0, m, 1, m / 2, &first, &end));
    assert(!stray_va_second_hint(0, m, 1, m, &first, &end));
    assert(!stray_va_second_hint(8, 128, 1, 5, &first, &end));
    assert(!stray_va_second_hint(9, 128, 1, 3, &first, &end));
    assert(!stray_va_second_hint(8, 128, 0, 0, &first, &end));
    printf("%llu aligned-window comparisons and 8 overflow/invalid cases passed.\n",
           (unsigned long long)checked);
}

static uint32_t rng(uint32_t *state)
{
    *state = *state * UINT32_C(1664525) + UINT32_C(1013904223);
    return *state;
}

static void fragmented(void)
{
    unsigned char slots[LIMIT];
    unsigned counts[3] = {0};
    uint32_t state = 5;
    for (unsigned test = 0; test < 26000; ++test)
    {
        for (unsigned i = 0; i < LIMIT; ++i) slots[i] = (rng(&state) >> 24) < (test % 201);
        uintptr_t hi = 40 + (rng(&state) % (LIMIT - 40));
        uintptr_t size = (rng(&state) >> 16) % 22 + 1, step = 1u << ((rng(&state) >> 20) % 4);
        struct stray_va_cursor c = {1, 2, LO, hi, LO + (rng(&state) >> 10) % (hi - LO), 64};
        struct stray_va_cursor copy = c;
        struct outcome old = map_one(slots, &copy, hi, size, step - 1, 0, 1);
        struct outcome now = map_one(slots, &c, hi, size, step - 1, 1, 1);
        assert(old.first == now.first && now.first <= 16 && now.second <= 240);
        assert(!!old.address == !!now.address);
        if (old.path == FIRST || now.path != SECOND) assert(old.address == now.address);
        if (now.path == FULL) assert(old.full == now.full);
        if (now.address)
        {
            assert(now.address >= LO && now.address <= hi - size && !(now.address & (step - 1)));
            for (uintptr_t i = 0; i < size; ++i) assert(!slots[now.address+i]);
        }
        ++counts[now.path];
    }
    assert(counts[FULL] && counts[FIRST] && counts[SECOND]);
    printf("26000 fragmented comparisons passed (full %u, first %u, second %u).\n",
           counts[FULL], counts[FIRST], counts[SECOND]);
}

static void sequences(void)
{
    unsigned char map[LIMIT];
    memset(map, 1, sizeof(map));
    memset(map + 530, 0, 5);
    struct stray_va_cursor c = {1, 2, LO, LIMIT, 500, 64}, original = c;
    struct outcome r = map_one(map, &c, LIMIT, 5, 0, 1, 0);
    assert(r.path == SECOND && r.address == 530 && r.first == 16 && !r.full);
    assert(c.next == original.next && c.remaining == original.remaining);
    struct outcome old = map_one(map, &c, LIMIT, 5, 0, 0, 0);
    assert(old.path == FULL && old.address == 530);
    printf("Fragmented prefix: %u candidate checks vs %u with the extra window.\n",
           old.first + old.full, r.first + r.second);

    // All candidates miss: the full scan still returns the low free address.
    memset(map, 1, sizeof(map));
    memset(map + LO, 0, 5);
    r = map_one(map, &c, LIMIT, 5, 0, 1, 1);
    assert(r.path == FULL && r.address == LO && r.first == 16 && r.second == 240 && !c.remaining);
    printf("No-gain case: 240 extra rejected starts before the unchanged low address.\n");
    // A reclaimed low hole is retried after the original 64-hit cursor lease.
    memset(map, 0, sizeof(map));
    c = (struct stray_va_cursor){1, 2, LO, LIMIT, 400, 64};
    for (unsigned i = 0; i < 64; ++i)
    {
        map[c.next] = 1;
        r = map_one(map, &c, LIMIT, 1, 0, 1, 1);
        assert(r.path == FIRST && r.address != LO);
        map[r.address] = 1;
    }
    assert(c.remaining == 0);
    r = map_one(map, &c, LIMIT, 1, 0, 1, 1);
    assert(r.path == FULL && r.address == LO && !r.first && !r.second);
    // Stale owner, wrong window, and exhausted process are still excluded by stage 1.
    struct stray_va_cursor invalid[3] = {{3,2,LO,LIMIT,400,64},{1,9,LO,LIMIT,400,64},{1,2,LO,LIMIT-1,400,64}};
    for (unsigned i = 0; i < 3; ++i)
    {
        r = map_one(map, &invalid[i], LIMIT, 1, 0, 1, 0);
        assert(r.path == FULL && r.address == LO && !r.first && !r.second);
    }
    memset(map, 1, sizeof(map));
    c = original;
    r = map_one(map, &c, LIMIT, 1, 0, 1, 1);
    assert(!r.address && c.next == original.next && c.remaining == original.remaining);
    puts("Lease expiry, failure, owner/range isolation, and delayed publication passed.");
}

int main(void)
{
    geometry();
    fragmented();
    sequences();
}
