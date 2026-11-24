/* A search hint, never a reservation. All access is under virtual_mutex. */
struct stray_va_cursor
{
    uintptr_t peb, pid, lo, hi, next;
    unsigned int remaining;
};

static int stray_va_hint(const struct stray_va_cursor *c, uintptr_t peb, uintptr_t pid,
                         uintptr_t lo, uintptr_t hi, uintptr_t size, uintptr_t mask,
                         uintptr_t *first, uintptr_t *end)
{
    uintptr_t a, space, extra;
    if (!c->remaining || c->peb != peb || c->pid != pid || c->lo != lo || c->hi != hi ||
        c->next < lo || c->next >= hi || !size || mask == UINTPTR_MAX ||
        (mask & (mask + 1)) || mask > UINTPTR_MAX / 16 || c->next > UINTPTR_MAX - mask)
        return 0;
    a = (c->next + mask) & ~mask;
    if (a >= hi || size > hi - a) return 0;
    space = hi - a - size;
    extra = (mask + 1) * 15; /* Search up to 16 aligned starts. */
    *first = a;
    *end = a + size + (space < extra ? space : extra);
    return 1;
}

static void stray_va_result(struct stray_va_cursor *c, uintptr_t peb, uintptr_t pid,
                            uintptr_t lo, uintptr_t hi, uintptr_t placed, uintptr_t size,
                            int used_hint, unsigned int original_rejects)
{
    if (placed < lo || placed >= hi || !size || size > hi - placed ||
        (!used_hint && original_rejects < 64))
    {
        c->remaining = 0;
        return;
    }
    *c = (struct stray_va_cursor){peb, pid, lo, hi, placed + size,
                                 used_hint ? c->remaining - 1 : 64};
}
