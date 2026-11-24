#include <assert.h>
#include <errno.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

/* @production_helpers */

enum { TEST_LO = 8, TEST_HI = 1024 };
static unsigned char memory[TEST_HI];
static struct ios_af_rec { int stage; } ios_af;
static unsigned int ios_va_scan_tries, ios_va_scan_below_floor, ios_va_scan_above_ceiling, ios_va_scan_skips;
static unsigned int full_calls, bounded_calls, bounded_steps[2], full_steps;
static uintptr_t full_result;
static uint64_t clock_ns, model_full_ns;
static int wine_owns_views;
static struct stray_va_cursor ios_stray_va_cursor;
static uint64_t stray_va_now(void) { return ++clock_ns; }

static void *model_map(uintptr_t lo, uintptr_t hi, uintptr_t size, uintptr_t mask, unsigned *steps)
{
    for (uintptr_t a = (lo + mask) & ~mask; a < hi && size <= hi - a; a += mask + 1)
    {
        uintptr_t i = 0;
        ++*steps;
        while (i < size && !memory[a+i]) ++i;
        if (i == size) return (void *)a;
        if (!wine_owns_views) ++ios_va_scan_tries;
        errno = EEXIST;
        ios_af.stage = 41;
    }
    return NULL;
}

static void *map_free_area_inner(void *lo, void *hi, uintptr_t size, int top, int prot, uintptr_t mask, int bounded)
{
    assert(!top && prot == 3 && bounded && bounded_calls < 2);
    return model_map((uintptr_t)lo, (uintptr_t)hi, size, mask, &bounded_steps[bounded_calls++]);
}

static void *map_free_area(void *lo, void *hi, uintptr_t size, int top, int prot, uintptr_t mask)
{
    assert(!top && prot == 3 && (uintptr_t)lo == TEST_LO && (uintptr_t)hi == TEST_HI);
    assert(errno == EDOM && ios_af.stage == 9);
    clock_ns += model_full_ns; ++full_calls;
    full_result = (uintptr_t)model_map((uintptr_t)lo, (uintptr_t)hi, size, mask, &full_steps);
    return (void *)full_result;
}

static uintptr_t allocate(uintptr_t owner, uintptr_t guest, uintptr_t host_size, int create_ok,
                          struct stray_va_stats *stray_report)
{
    struct { struct { uintptr_t UniqueProcess; } ClientId; } fake_teb = {{guest}}, *teb = &fake_teb;
    void *peb = (void *)owner, *start = (void *)(uintptr_t)TEST_LO, *end = (void *)(uintptr_t)TEST_HI, *ptr = NULL;
    uintptr_t align_mask = 0;
    int unix_prot = 3, publish_cursor = 0;
    struct stray_va_cursor proposed_cursor;
    unsigned int tries0 = ios_va_scan_tries, below0 = 0, above0 = 0, skips0 = 0;
    errno = EDOM;
    ios_af.stage = 9;
    full_calls = bounded_calls = full_steps = bounded_steps[0] = bounded_steps[1] = 0;
    full_result = 0;
    /* @production_path */
    (void)skips0;
    if (create_ok && publish_cursor) ios_stray_va_cursor = proposed_cursor;
    return (uintptr_t)ptr;
}

static void seed(uintptr_t owner, uintptr_t guest, uintptr_t next)
{
    ios_stray_va_cursor = (struct stray_va_cursor){owner,guest,TEST_LO,TEST_HI,next,64,0};
}


static void test_pressure(void)
{
    struct stray_va_stats report = {0}, *s = stray_va_stats_for(231,110);
    struct stray_va_pressure p = {0};
    for (int i=0; i<63; i++) assert(!stray_va_pressure_result(&p,8,1024,1000,0,0));
    assert(!stray_va_pressure_result(&p,8,1024,1000000000,0,0)); /* one OS deschedule */
    for (int i=0; i<63; i++) assert(!stray_va_pressure_result(&p,8,1024,70000,0,0));
    assert(stray_va_pressure_result(&p,8,1024,70000,0,0));
    assert(!stray_va_pressure_result(&p,12,1024,70000,0,0)); /* new window */
    assert(!p.slow && p.samples==1 && !p.failures);
    memset(memory,0,sizeof(memory));
    wine_owns_views=1;
    model_full_ns=67000; /* simulate repeatedly expensive full searches */
    unsigned full=0, tries=0, total_steps=0, exact_steps=0, baseline_steps=0;
    for (unsigned i=0; i<915; i++)
    {
        uintptr_t pos=allocate(231,110,1,1,&report);
        assert(pos==TEST_LO+i); /* free placement matches the unmodified mapper in this layout */
        memory[pos]=1;
        full+=full_calls; tries+=bounded_calls;
        total_steps+=full_steps; exact_steps+=bounded_steps[0]; baseline_steps+=i+1;
        assert(!bounded_steps[1] && bounded_steps[0]<=1);
    }
    assert(full==114 && tries==801 && tries==s->early_hits && s->early_hits==s->early_tries);
    assert(s->second_tries==0 && s->expensive==0);
    printf("Ascending Wine views, 915 allocations: full=%u of 915, exact_hits=%u, inspected=%u vs. %u model starts.\n",
           full,tries,total_steps+exact_steps,baseline_steps);
    memset(memory,1,sizeof(memory)); memory[TEST_LO]=0;
    uint64_t before=s->early_tries;
    unsigned extra=0;
    for (unsigned i=0;i<4096;i++)
    {
        assert(allocate(231,110,1,1,&report)==TEST_LO && full_calls==1 && full_steps==1);
        assert(bounded_steps[0]<=1 && !bounded_steps[1]);
        extra+=bounded_steps[0];
    }
    assert(extra <= 72 && s->early_tries-before==extra);
    printf("Persistent occupied next slot, 4096 allocations: %u added failed exact starts; all fell back correctly.\n",extra);
    model_full_ns=999;
    for (int i=0;i<128;i++) assert(allocate(231,110,1,1,&report)==TEST_LO);
    assert(!s->pressure.slow);
    before=s->early_tries;
    for (int i=0;i<1024;i++) assert(allocate(231,110,1,1,&report)==TEST_LO && !bounded_calls);
    assert(s->early_tries==before);
    struct stray_va_cursor old=ios_stray_va_cursor;
    for (int i=0;i<128;i++) { model_full_ns=68000; assert(allocate(231,110,1,0,&report)==TEST_LO); }
    assert(!memcmp(&old,&ios_stray_va_cursor,sizeof(old)));
    /* Fallback retains its result/error even when neither path finds space. */
    memset(memory,1,sizeof(memory)); seed(231,110,68); ios_stray_va_cursor.exact_only=1;
    assert(!allocate(231,110,1,1,&report) && full_calls==1 && bounded_steps[0]==1 && !bounded_steps[1]);
    wine_owns_views=0; model_full_ns=0;
}
int main(void)
{
    struct stray_va_stats snapshot = {0}, *a = stray_va_stats_for(17,5), *b = stray_va_stats_for(81,6);
    memset(memory, 1, sizeof(memory));
    memory[521] = 0;
    seed(17,5,500);
    assert(allocate(17,5,1,0,&snapshot) == 521);
    assert(bounded_calls == 2 && bounded_steps[0] == 16 && bounded_steps[1] == 6 && !full_calls);
    assert(a->hits == 1 && a->second_hits == 1 && a->second_tries == 1);
    assert(!a->full && !a->misses && a->second_ns == 1 && a->hint_ns == 2 && snapshot.peb == 17);
    assert(ios_stray_va_cursor.next == 500 && ios_stray_va_cursor.remaining == 64);
    assert(!b->hits && !b->second_tries);
    memset(memory, 1, sizeof(memory));
    memory[TEST_LO] = 0;
    unsigned total_rejected = 0;
    for (unsigned i=0; i<4096; ++i)
    {
        /* The real cursor is deliberately reseeded on every request to model
         * a persistent fragmented neighborhood while the guest remains live. */
        seed(17,5,500);
        assert(allocate(17,5,1,1,&snapshot) == TEST_LO);
        assert(bounded_steps[0] == 16 && full_calls == 1 && full_steps == 1);
        if (i==0) assert(bounded_steps[1] == 32);
        if (i==8) assert(bounded_steps[1] == 64);
        if (i==24) assert(bounded_steps[1] == 128);
        if (i==56) assert(bounded_steps[1] == 240);
        total_rejected += bounded_steps[1];
        assert(ios_stray_va_cursor.remaining == 0);
    }
    assert(a->full == 4096 && a->hits == 1 && !a->expensive && a->misses == 4096);
    assert(a->second_tries + a->second_skips == 4097 && a->second_hits == 1);
    assert(a->second_ns == a->second_tries && a->hint_ns == 4097 + a->second_tries);
    assert(total_rejected < 4 * 4096);
    printf("Production mapper call path: 4096 forced first-window misses, %u extra starts, %llu probes, %llu skipped.\n",
           total_rejected, (unsigned long long)(a->second_tries - 1), (unsigned long long)a->second_skips);
    assert(!b->second_policy.misses);
    memset(memory, 1, sizeof(memory));
    memory[525] = 0;
    seed(81,6,500);
    assert(allocate(81,6,1,1,&snapshot) == 525);
    assert(b->second_hits == 1 && b->second_tries == 1 && ios_stray_va_cursor.next == 526);
    // One previous miss makes room for a farther hit after seven skipped opportunities.
    memset(memory, 1, sizeof(memory));
    memory[TEST_LO] = 0;
    memory[576] = 0;
    for (unsigned i=0; i<=8; ++i)
    {
        seed(81,6,500);
        uintptr_t placement = allocate(81,6,1,1,&snapshot);
        if (i<8) assert(placement == TEST_LO && full_calls == 1);
        else assert(placement == 576 && !full_calls && b->second_policy.width == 64);
    }
    assert(!b->second_policy.misses && !b->second_policy.skip);
    seed(81,6,TEST_HI-12);
    assert(allocate(81,6,1,1,&snapshot)==TEST_LO && bounded_calls==1);
    memset(memory,0,sizeof(memory));
    seed(81,6,500);
    assert(allocate(81,6,1,1,&snapshot)==500 && bounded_calls==1 && !full_calls);
    struct stray_va_second_policy policy = { .lo=8, .hi=1024, .misses=4, .skip=63, .width=240 };
    uintptr_t hi=60;
    assert(stray_va_second_admit(&policy,16,1024,20,1,1,&hi));
    assert(!policy.skip && !policy.misses && policy.width==32 && hi==52);
    test_pressure();
    puts("Actual production selection, original fallback error/counters, owner/window isolation, retry, range, and success handling passed.");
}
