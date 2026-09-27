# Experiment 3 Phase 0 — Haar reconstruction topologies at 1984x896 4:4:4 (DERIVED model)

Haar: inverse `x0 = a - d/2`, `x1 = a + d/2`, 2-tap, no apron, so tiles are independent across levels. Output tile 32x32 per 64-lane workgroup in every stage; a stage fusing k levels starts from a (32 >> k)^2 LL tile. Bytes count PyroWave's dequant writes, every coefficient read once, inter-stage LL writes and re-reads, and the R8 output. Physical DRAM traffic is UNKNOWN; register counts are ESTIMATED.

| topology | stages | dispatches | global barriers | workgroup barriers | temp LL images | coefficient reads | LL writes | LL re-reads | total logical bytes | bytes/px | vs 9/7 | full-frame passes | max shared | regs/lane (est) | min lane util |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 9/7 compute (reference) | 5 | 15 | 6 | 15 | 4 | - | - | - | 35,497,728 | 19.97 | 0.0 % | 2.27 | 3,280 | - | - |
| H0 [1, 1, 1, 1, 1] | 5 | 15 | 6 | 15 | 4 | 14,207,424 | 3,541,440 | 3,541,440 | 33,747,840 | 18.98 | -4.9 % | 2.16 | 3,280 | 16 | 1.000 |
| H1 [2, 2, 1] | 3 | 9 | 4 | 13 | 2 | 13,499,136 | 2,833,152 | 2,833,152 | 32,331,264 | 18.19 | -8.9 % | 2.03 | 3,280 | 16 | 1.000 |
| H2 [3, 2] | 2 | 6 | 3 | 12 | 1 | 11,332,608 | 666,624 | 666,624 | 27,998,208 | 15.75 | -21.1 % | 1.62 | 2,048 | 16 | 0.250 |
| H3 [5] | 1 | 3 | 2 | 11 | 0 | 10,665,984 | 0 | 0 | 26,664,960 | 15.00 | -24.9 % | 1.50 | 2,048 | 16 | 0.016 |

## Per-stage detail

- H0 stage 1: levels 5->4 (1 fused), tile 16^2 -> 32^2, reads 41,664 B, writes 41,664 B, shared 3,280 B, regs/lane ~16, lane utilisation per level [1.0], workgroup barriers 3
- H0 stage 2: levels 4->3 (1 fused), tile 16^2 -> 32^2, reads 166,656 B, writes 166,656 B, shared 3,280 B, regs/lane ~16, lane utilisation per level [1.0], workgroup barriers 3
- H0 stage 3: levels 3->2 (1 fused), tile 16^2 -> 32^2, reads 666,624 B, writes 666,624 B, shared 3,280 B, regs/lane ~16, lane utilisation per level [1.0], workgroup barriers 3
- H0 stage 4: levels 2->1 (1 fused), tile 16^2 -> 32^2, reads 2,666,496 B, writes 2,666,496 B, shared 3,280 B, regs/lane ~16, lane utilisation per level [1.0], workgroup barriers 3
- H0 stage 5: levels 1->0 (1 fused), tile 16^2 -> 32^2, reads 10,665,984 B, writes 5,332,992 B, shared 3,280 B, regs/lane ~16, lane utilisation per level [1.0], workgroup barriers 3
- H1 stage 1: levels 5->3 (2 fused), tile 8^2 -> 32^2, reads 166,656 B, writes 166,656 B, shared 2,048 B, regs/lane ~16, lane utilisation per level [1.0, 1.0], workgroup barriers 5
- H1 stage 2: levels 3->1 (2 fused), tile 8^2 -> 32^2, reads 2,666,496 B, writes 2,666,496 B, shared 2,048 B, regs/lane ~16, lane utilisation per level [1.0, 1.0], workgroup barriers 5
- H1 stage 3: levels 1->0 (1 fused), tile 16^2 -> 32^2, reads 10,665,984 B, writes 5,332,992 B, shared 3,280 B, regs/lane ~16, lane utilisation per level [1.0], workgroup barriers 3
- H2 stage 1: levels 5->2 (3 fused), tile 4^2 -> 32^2, reads 666,624 B, writes 666,624 B, shared 2,048 B, regs/lane ~16, lane utilisation per level [0.25, 1.0, 1.0], workgroup barriers 7
- H2 stage 2: levels 2->0 (2 fused), tile 8^2 -> 32^2, reads 10,665,984 B, writes 5,332,992 B, shared 2,048 B, regs/lane ~16, lane utilisation per level [1.0, 1.0], workgroup barriers 5
- H3 stage 1: levels 5->0 (5 fused), tile 1^2 -> 32^2, reads 10,665,984 B, writes 5,332,992 B, shared 2,048 B, regs/lane ~16, lane utilisation per level [0.016, 0.062, 0.25, 1.0, 1.0], workgroup barriers 11

## Structural gate (feasible AND material)

Feasibility: shared x 2 workgroups <= 32,768 B (device limit to be logged in Phase 2), registers/lane <= 64 (ESTIMATED), redundant loads <= 1.25x. Material: logical bytes cut >= 30 % vs 9/7 compute, or global barriers AND full-frame passes both cut >= 30 % (the brief's 'comparably substantial'). The byte cut includes precision 0 (all-R16F, worth 4.9 % on its own, Experiment 2); the structural part is the difference from H0.

- H0: bytes -4.9 % vs 9/7 (bytes_ok False); global barriers -0.0 %, full-frame passes -4.5 % (structure_ok False); feasible True  -> **NOT STRONG ENOUGH**
- H1: bytes -8.9 % vs 9/7 (bytes_ok False); global barriers -33.3 %, full-frame passes -10.3 % (structure_ok False); feasible True  -> **NOT STRONG ENOUGH**
- H2: bytes -21.1 % vs 9/7 (bytes_ok False); global barriers -50.0 %, full-frame passes -28.3 % (structure_ok False); feasible True  -> **NOT STRONG ENOUGH**
- H3: bytes -24.9 % vs 9/7 (bytes_ok False); global barriers -66.7 %, full-frame passes -33.8 % (structure_ok True); feasible True  -> **PROCEED**

Topologies that proceed to the microbenchmark: H3.

Lane utilisation at the coarse levels of a deep stage is the known cost of fusion: the first Haar level of H3 reconstructs a 2x2 tile with 1 of 64 lanes active. It is cheap in absolute terms (the coarse levels hold 1/1024 .. 1/16 of the pixels) but it is real, ESTIMATED here and measured in Phase 2.
