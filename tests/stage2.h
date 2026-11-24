/* Called only after stray_va_hint supplied first and that probe missed.
 * Both ranges bound aligned candidate starts, with end exclusive for memory. */
static int stray_va_second_hint(uintptr_t first, uintptr_t hi, uintptr_t size,
                                uintptr_t mask, uintptr_t *next, uintptr_t *end)
{
    uintptr_t step, last_start, room_steps, second, extra_steps;
    if (!size || first >= hi || size > hi - first || mask == UINTPTR_MAX ||
        (mask & (mask + 1)) || (first & mask))
        return 0;
    step = mask + 1;
    last_start = hi - size;
    room_steps = (last_start - first) / step;
    if (room_steps < 16) return 0;
    second = first + 16 * step;
    extra_steps = room_steps - 16;
    if (extra_steps > 239) extra_steps = 239;
    *next = second;
    *end = second + extra_steps * step + size;
    return 1;
}

struct stray_va_second_policy
{
    uintptr_t lo, hi;
    unsigned int width, misses, skip;
};

/* Count only eligible first-window misses. Normal first-window hits and
 * near-ceiling requests incur no backoff cost and do not reset the policy. */
static int stray_va_second_admit(struct stray_va_second_policy *p, uintptr_t lo, uintptr_t hi,
                                 uintptr_t first, uintptr_t size, uintptr_t step, uintptr_t *end)
{
    uintptr_t starts;
    if (p->lo != lo || p->hi != hi)
        *p = (struct stray_va_second_policy){ .lo = lo, .hi = hi, .width = 32 };
    if (p->skip)
    {
        --p->skip;
        return 0;
    }
    starts = (*end - first - size) / step + 1;
    if (starts > p->width) *end = first + (p->width - 1) * step + size;
    return 1;
}

static void stray_va_second_result(struct stray_va_second_policy *p, uintptr_t placed,
                                   uintptr_t first, uintptr_t step)
{
    if (placed)
    {
        uintptr_t n = (placed - first) / step + 1;
        p->width = n <= 32 ? 32 : n <= 64 ? 64 : n <= 128 ? 128 : 240;
        p->misses = p->skip = 0;
        return;
    }
    if (p->misses < 4) ++p->misses;
    p->skip = (1u << (p->misses + 2)) - 1; /* 7, 15, 31, then 63 eligible misses. */
    if (p->width < 128) p->width *= 2;
    else p->width = 240;
}
