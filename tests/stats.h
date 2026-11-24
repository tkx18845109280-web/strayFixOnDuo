/* Updated with virtual_mutex held. A bounded table keeps the counters of simultaneous
 * Stray guest instances separate. New instances replace old entries after eight distinct keys. */
struct stray_va_stats
{
    uintptr_t peb, pid;
    uint64_t full, expensive, hits, misses, full_ns, hint_ns;
};

static void stray_va_account( struct stray_va_stats *report, uintptr_t peb, uintptr_t pid,
                              int hinted, int hit, unsigned int full_rejects,
                              uint64_t hint_ns, uint64_t full_ns )
{
    static struct stray_va_stats slots[8];
    static unsigned int next;
    struct stray_va_stats *s;
    uint64_t requests;
    unsigned int i;
    int expensive = !hit && full_rejects >= 64;

    for (i = 0; i < 8; ++i)
        if (slots[i].peb == peb && slots[i].pid == pid) break;
    if (i == 8)
    {
        i = next++ % 8;
        slots[i] = (struct stray_va_stats){ .peb = peb, .pid = pid };
    }
    s = &slots[i];
    s->full += !hit;
    s->expensive += expensive;
    s->hits += hit;
    s->misses += hinted && !hit;
    s->full_ns += full_ns;
    s->hint_ns += hint_ns;
    requests = s->full + s->hits;
    if (requests == 1 || (expensive && s->expensive == 1) ||
        (requests >= 128 && !(requests & (requests - 1))))
        *report = *s;
}
