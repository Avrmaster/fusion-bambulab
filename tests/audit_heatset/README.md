# Independent audit of the heat-set gate

Shares no code with the gate. It feels every pocket the gate listed with `pointContainment` (8 directions, every
0.03 mm of depth, bisection to 0.005 mm), finds the inserts by a point inside their wall, judges the felt profile with
its own code and compares, pocket by pocket, with the gate's findings. Run it after a change to the gate:

    python3 tests/audit_heatset/audit_run.py <project>      # about 1 s per pocket in Fusion, batches of 40 s
    python3 tests/audit_heatset/audit_judge.py <project>    # agree / differ, with both profiles for each difference

Robot Arm V2, 2026-11-23: 142 pockets, 138 agree. The 4 that differ: 2 in J1 base where both see a non-standard relief
(two stacked cuts with a sliver; the gate names `relief_depth` + `not_round`), 2 in the J2 encoder column where the
audit sees a wall under 0.25 mm between the relief and a D12.5 recess (wall thickness is not this gate's question).
Known limits of the audit: its 0.03 mm step makes its depth tolerance 0.075 (the gate's is 0.06), and an insert is
found only by a point 2 mm from the axis.
