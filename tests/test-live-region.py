"""Run the shipped VA collision loop against deterministic mock Mach responses."""
import argparse
from pathlib import Path
import shutil
import subprocess
import tempfile


def section(source, start, end):
    pos = source.index(start)
    return source[pos:source.index(end, pos)]


PRELUDE = r"""
#include <assert.h>
#include <errno.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define WINE_IOS 1
#define MAP_FAILED ((void *)-1)
#define TRACE(...) ((void)0)
#define ERR(...) ((void)0)
#define dprintf(...) ((void)0)
#define ROUND_ADDR(p,m) ((void *)((uintptr_t)(p) & ~(uintptr_t)(m)))
#define KERN_SUCCESS 0
#define KERN_INVALID_ADDRESS 1
#define MACH_PORT_NULL 0
#define MACH_PORT_VALID(p) ((p)!=0)
#define VM_REGION_BASIC_INFO_64 0
#define VM_REGION_BASIC_INFO_COUNT_64 0
typedef uintptr_t mach_vm_address_t, mach_vm_size_t;
typedef unsigned mach_msg_type_number_t, mach_port_t;
typedef int vm_region_basic_info_data_64_t, *vm_region_info_t;
static void *ios_jit_rx_base_global, *ios_jit_rw_base_global, *ios_scan_fail_addr;
static size_t ios_jit_pool_size_global, ios_jit_low_size_global;
static uintptr_t ios_jit_low_rw_global, ios_jit_low_rx_global, ios_layerkit_lo, ios_layerkit_hi;
static unsigned ios_va_scan_tries, ios_va_scan_skips, ios_va_scan_below_floor,
                ios_va_scan_above_ceiling, ios_layerkit_skips;
static int ios_scan_fail_errno;
static int ios_layerkit_enforce;
static int ios_in_layerkit(uint64_t a,size_t z){return ios_layerkit_enforce && ios_layerkit_hi && a<ios_layerkit_hi && a+z>ios_layerkit_lo;}
static uintptr_t ios_va_map_floor(void){return 0;}
static uintptr_t ios_va_map_ceiling(int x){(void)x;return 0;}
static int ios_va_no_region_in(uintptr_t l,uintptr_t h){(void)l;(void)h;return 1;}
static void ios_af_set(int s,int k,void *a,size_t n){(void)s;(void)k;(void)a;(void)n;}
static int mach_task_self(void){return 1;}
static int deallocations;
static int mach_port_deallocate(int task,mach_port_t p){(void)task;assert(p==77);++deallocations;return 0;}
#define GRAN ((uintptr_t)0x10000)
#define BASE ((uintptr_t)0x703ab30000)
#define CELLS 9008
static unsigned char occupied[CELLS], denied[CELLS];
static int maps, queries, fail_query, insert_cell, insert_done, claim_cell=-1, release_cell=-1, passes;
static uintptr_t ios_exe_win_base, ios_exe_win_size;
static int ios_exe_win_state, grant_port, captured_rights;
static int cell(uintptr_t a){assert(a>=BASE && a<BASE+CELLS*GRAN);return (int)((a-BASE)/GRAN);}
static int mach_vm_region(int task,mach_vm_address_t *a,mach_vm_size_t *n,int f,vm_region_info_t out,
                          mach_msg_type_number_t *count,mach_port_t *port) {
    (void)task;(void)f;(void)out;(void)count;*port=0;++queries;
    int i=cell(*a);
    if(i==fail_query)return 1;
    while(i<CELLS && !occupied[i])++i;
    if(i==CELLS)return 1;
    *a=BASE+i*GRAN;*n=GRAN;
    if(grant_port){*port=77;++captured_rights;}
    return 0;
}
static void *anon_mmap_tryfixed(void *p,size_t n,int prot,int flags) {
    (void)prot;(void)flags;
    int i=cell((uintptr_t)p); ++maps;
    if(i==claim_cell)occupied[i]=0;
    if(i==insert_cell && !insert_done){occupied[i]=1;insert_done=1;}
    if(denied[i]){errno=ENOMEM;return MAP_FAILED;}
    for(unsigned k=0;k<n/GRAN;k++)if(occupied[i+k]){errno=EEXIST;return MAP_FAILED;}
    return p;
}
"""

MAIN = r"""
static void scenario(const char *name, int occ, int refuse, int hi, int size, int query_error,
                     int insertion, int top, int step0) {
    memset(occupied,0,sizeof(occupied));memset(denied,0,sizeof(denied));
    memset(occupied,1,occ);memset(denied+occ,1,refuse);
    if(!strcmp(name,"starts-in-hole")){occupied[occ+1]=1;}
    if(!strcmp(name,"freed-hole")){memset(occupied+9,0,4);}
    maps=queries=insert_done=deallocations=captured_rights=0;fail_query=query_error;insert_cell=insertion;
    grant_port=!strcmp(name,"port-returned");passes=0;claim_cell=release_cell=-1;
    ios_exe_win_state=0;ios_exe_win_base=ios_exe_win_size=0;
    ios_layerkit_lo=ios_layerkit_hi=ios_layerkit_enforce=ios_layerkit_skips=0;
    ios_va_scan_tries=ios_va_scan_skips=0;ios_scan_fail_addr=NULL;ios_scan_fail_errno=errno=0;
    INIT_SCAN;
    void *lo=(void*)BASE,*end=(void*)(BASE+hi*GRAN);
    void *first=top?(void*)(BASE+(hi-size)*GRAN):lo;
    ptrdiff_t step=step0?0:(top?-(ptrdiff_t)GRAN:(ptrdiff_t)GRAN);
    void *found=NULL;
    if(!strncmp(name,"wrapper-",8)){
        if(strstr(name,"layerkit")){
            ios_layerkit_enforce=1;ios_layerkit_lo=BASE+(hi-2)*GRAN;ios_layerkit_hi=BASE+hi*GRAN;
        }
        if(!strcmp(name,"wrapper-claim")||!strcmp(name,"wrapper-uninitialised")){
            claim_cell=12;ios_exe_win_base=BASE+12*GRAN;ios_exe_win_size=GRAN;
            if(!strcmp(name,"wrapper-uninitialised")){ios_exe_win_state=-1;ios_exe_win_base=0;}
        }
        if(!strcmp(name,"wrapper-released-during-retry"))release_cell=12;
        found=CALL_WRAPPER(lo,end,size*GRAN,top,3,GRAN-1);
    }else if(!strncmp(name,"split-",6)){
        int group=name[6]-'0';assert(size==1 && !top);
        for(int i=0;i<hi && !found;i+=group){
            int upper=(i+group<hi?i+group:hi);
            void *left=(void*)(BASE+i*GRAN),*right=(void*)(BASE+upper*GRAN);
            found=CALL_SCAN(left,right,step,left,size*GRAN,3);
        }
    }else found=CALL_SCAN(lo,end,step,first,size*GRAN,3);
    int pos=found?cell((uintptr_t)found):-1;
    if(pos>=0){for(int j=pos;j<pos+size;j++)assert(!occupied[j]);assert(!denied[pos]);}
    printf("%s %d %d %d %u %u %d %d %d %d %d %d %d\n",name,pos,maps,queries,ios_va_scan_tries,
           ios_va_scan_skips,ios_scan_fail_errno,insert_done,captured_rights,deallocations,passes,errno,COOL);
}
int main(void){
    scenario("first-free",0,0,20,4,-1,-1,0,0);
    scenario("device-count-shape",8752,43,8850,4,-1,-1,0,0);
    scenario("exhausted",17,0,17,4,-1,-1,0,0);
    scenario("query-failure",16,2,30,4,10,-1,0,0);
    scenario("starts-in-hole",10,0,25,4,-1,-1,0,0);
    scenario("collision-on-map",12,0,26,4,-1,12,0,0);
    scenario("freed-hole",22,0,26,4,-1,-1,0,0);
    scenario("top-down",17,0,21,4,-1,-1,1,0);
    scenario("fixed",1,0,20,4,-1,-1,0,1);
    scenario("split-1",8752,43,8850,1,-1,-1,0,0);
    scenario("split-4",8752,43,8850,1,-1,-1,0,0);
    scenario("port-returned",37,0,46,4,-1,-1,0,0);
    scenario("wrapper-persistent",8752,0,8752,1,-1,-1,0,0);
    scenario("wrapper-race-priming",0,0,35,1,-1,-1,0,0);
    scenario("wrapper-released-during-retry",35,0,35,1,-1,-1,0,0);
    scenario("wrapper-guard-priming",0,0,22,1,-1,-1,0,0);
    scenario("wrapper-claim",20,0,22,1,-1,-1,0,0);
    scenario("wrapper-uninitialised",20,0,22,1,-1,-1,0,0);
    scenario("wrapper-layerkit",20,0,22,1,-1,-1,0,0);
    scenario("wrapper-device-cold",8752,43,8850,4,-1,-1,0,0);
    scenario("wrapper-device-warm",8752,43,8850,4,-1,-1,0,0);
    scenario("wrapper-larger",8752,43,8850,8,-1,-1,0,0);
    scenario("wrapper-different-window",8752,43,8851,4,-1,-1,0,0);
    scenario("wrapper-seed",35,0,36,1,-1,-1,0,0);
    scenario("wrapper-transition-full",36,0,36,1,-1,-1,0,0);
    for(int i=0;i<32;i++){
        char label[64];sprintf(label,"wrapper-cool-%02d",i);
        scenario(label,i%4==0?36:35,0,36,1,-1,-1,0,0);
    }
    scenario("wrapper-after-cool",35,0,36,1,-1,-1,0,0);
    scenario("wrapper-insert-priming",0,0,26,4,-1,-1,0,0);
    scenario("wrapper-insert-while-mapping",12,0,26,4,-1,12,0,0);
}
"""


def replay(source_path, tmp, name, cc):
    source = source_path.read_text(encoding="utf8")
    trial = "struct stray_region_scan" in source
    helper = section(source, "struct stray_region_history" if trial else "static int ios_skip_occupied( void *addr", "/* ml900:")
    scanner = section(source, "static void* try_map_free_area( void *base", "/***********************************************************************")
    start = source.index("static void *map_free_area_inner( void *base")
    end = source.index("static void *map_free_area_inner( void *base", start + 1)
    original_wrapper = source[start:end]
    signature = source[end:source.index("\n{", end)]
    wrapper_stub = signature + """
    {
        ++passes;
        if(passes==2 && release_cell>=0)occupied[release_cell]=0;
        return try_map_free_area(base,end,(ptrdiff_t)(align_mask+1),base,size,unix_prot EXTRA);
    }
    """.replace(" EXTRA", ",scan" if trial else "")
    c = tmp / (name + ".c")
    exe = tmp / (name + ".exe")
    if trial:
        macros = ("\n#define INIT_SCAN struct stray_region_scan scan={ios_va_scan_skips,0}\n"
                  "#define CALL_SCAN(a,b,c,d,e,f) try_map_free_area(a,b,c,d,e,f,&scan)\n"
                  "static struct stray_region_history history;\n#define COOL history.cooldown\n"
                  "#define CALL_WRAPPER(a,b,c,d,e,f) map_free_area(a,b,c,d,e,f,&history)\n")
    else:
        macros = "\n#define INIT_SCAN ((void)0)\n#define COOL 0\n#define CALL_SCAN try_map_free_area\n#define CALL_WRAPPER map_free_area\n"
    c.write_text(PRELUDE + helper + scanner + original_wrapper + wrapper_stub + macros + MAIN)
    subprocess.run([cc, "-std=c11", "-O2", str(c), "-o", str(exe)], check=True)
    output = subprocess.check_output([str(exe)], text=True)
    names = ("found_cell", "map_calls", "region_queries", "rejects", "skips", "first_errno", "insert_done",
             "nonzero_name_rights_returned", "nonzero_name_rights_released", "full_passes", "final_errno", "cooldown")
    rows = [line.split() for line in output.splitlines()]
    assert all(len(row) == len(names) + 1 for row in rows)
    return {row[0]: dict(zip(names, map(int, row[1:]))) for row in rows}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--cc", default="cc")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as dirname:
        tmp = Path(dirname)
        base = replay(args.baseline, tmp, "stock", args.cc)
        shapes = {"first-free": 0, "device-count-shape": 8795, "exhausted": -1, "query-failure": 18,
                  "starts-in-hole": 12, "collision-on-map": 13, "freed-hole": 9, "top-down": 17, "fixed": -1,
                  "split-1": 8795, "split-4": 8795, "port-returned": 37}
        shapes.update({"wrapper-persistent": -1, "wrapper-released-during-retry": -1,
                       "wrapper-race-priming": 0,"wrapper-guard-priming": 0,
                       "wrapper-claim": 12, "wrapper-uninitialised": 12,"wrapper-layerkit":20,
                       "wrapper-device-cold":8795,"wrapper-device-warm":8795,"wrapper-larger":8795,
                       "wrapper-different-window":8795,"wrapper-seed":35,"wrapper-transition-full":-1,
                       "wrapper-after-cool":35,"wrapper-insert-priming":0,"wrapper-insert-while-mapping":13})
        shapes.update({f"wrapper-cool-{i:02d}": -1 if i%4==0 else 35 for i in range(32)})
        assert {k: r["found_cell"] for k, r in base.items()} == shapes
        assert base["device-count-shape"]["rejects"] == 8795 and base["device-count-shape"]["skips"] == 8752
        new = replay(args.candidate, tmp, "candidate", args.cc)
        for k, r in base.items():
            if k == "wrapper-released-during-retry":
                assert new[k]["found_cell"] == 12 and new[k]["full_passes"] == 2
                continue
            for field in ("found_cell", "rejects", "skips", "first_errno", "insert_done"):
                assert new[k][field] == r[field], (k, field, r[field], new[k][field])
            if r["found_cell"] < 0:
                assert new[k]["final_errno"] == r["final_errno"]
            if k in ("wrapper-device-cold","wrapper-layerkit","wrapper-claim","wrapper-uninitialised",
                     "wrapper-larger","wrapper-different-window","wrapper-persistent") or k.startswith("wrapper-cool-"):
                assert new[k]["map_calls"] == r["map_calls"] and new[k]["region_queries"] == r["region_queries"], k
        assert new["wrapper-transition-full"]["cooldown"]==32 and new["wrapper-transition-full"]["full_passes"]==2
        assert new["wrapper-after-cool"]["map_calls"] < base["wrapper-after-cool"]["map_calls"]
        assert new["wrapper-device-warm"]["map_calls"] == 52
        assert new["port-returned"]["nonzero_name_rights_returned"] == new["port-returned"]["nonzero_name_rights_released"]

        print(f"{len(base)} address-space scenarios passed.")
