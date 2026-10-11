# Garment checks before sewing and simulation

This is a recommended workflow, not a claim that MD enforces these constraints.
API measurements remain in [md-conventions.md](md-conventions.md). Clothing-session
observations below were made on MD 2026.0.315 on September 21, 2026.

## Distinguish three different overlaps

- **2D board overlap:** two separate patterns occupy the same board area. This can
  obscure inspection but does not itself prove intersecting cloth in 3D.
- **Sewing overlap:** duplicate seam pairs or multiple attachments consume the same
  section of an edge. Investigate unless deliberately sewing a layered construction.
- **3D intersection:** simulated surfaces or hardware penetrate each other. Correct
  sewing does not establish collision clearance.

## Before adding seams

1. Save a checkpoint; read current unique piece names, indices and outline/child-line
   identities. Rebuild the map after import, deletion or topology edits.
2. Maintain a seam ledger naming each functional edge, the two counterparts, covered
   intervals and intended start/end correspondence. Include existing attachments,
   not just the new batch. If existing endpoints cannot be queried, inspect them in
   MD and record uncertain coverage; a partial ledger cannot certify no duplicates.
3. Reject identical/reversed duplicate pairs and sewing an edge to the same overlapping
   part of itself. Flag reused intervals. Document legitimate multilayer attachments;
   do not silently delete them. Adjacent, nonoverlapping intervals can be intentional.
4. Read actual edge lengths from MD; spline chord sums are not arc lengths. Compare
   attachment lengths with an explicit tolerance and intended ease/gather ratio.
   Unequal lengths are not automatically wrong for elastic bands, gathers or sleeve ease.
5. Work out endpoint correspondence from winding and intended anatomy. The measured
   (True, False) example is not a universal flag pair for mirrored/imported contours.
6. Check contours for self-crossing and zero-length edges. Curve samples need the
   actual MD curve interpretation; a straight polygon approximation is only a screen.
   After moving points, read all changed coordinates back before adding sewing.

`md_seams` reads the ledger back from MD: for each seam, both pieces, the span along
each outline in mm, the line numbers it covers, direction and lengths, with warnings for
an edge sewn to an overlapping part of itself, one stretch of edge in two seams, and
sides more than 5 mm apart. Pass the fingerprint of an earlier read as `since` to see the
seams added and removed since. It reports what MD holds; checking that against the plan
is still yours.

## After a small sewing batch

Check return values, count delta and per-piece memberships, then inspect actual edges
and directions. A matching count cannot prove correct pairings. An orphan or left/right
count difference is a review flag: loose accessories and asymmetric designs can be valid.

Start with separated, correctly oriented 3D panels near the intended body region.
Inspect normals, body penetration, layer order and collision thickness before a bounded
simulation. Do not hide a wrong seam by increasing collision offsets or freezing pieces.

Use appropriate mesh resolution for narrow trims. The Nardo session failed when roughly
3.85 mm-wide welts were assigned 5 mm particle distance; 1 mm welts and 2 mm facings
were local working values, not universal settings. Avoid large point-edit loops that
remesh after every move. Imported patterns also shifted their 2D positions, so internal
attachment guides must use read-back coordinates.

Inspect the full intended simulation state after temporary fitting aids are removed.
Check front/back/side and hidden waist/pocket joins. Distinguish zipper attachment from
surface clearance. Front-projection vertex tests do not certify all surfaces or poses.

## Recovery

The updated listener returns execution_succeeded, captured output and elapsed_seconds
for scripts, plus each available scene field. A readback_errors entry means the query
failed, not that the script did. Failed scripts preserve available partial state and
are not rolled back. Each reply includes the complete available piece list.

A timeout still cannot stop a native MD operation. Do not resubmit a mutation until the
original operation has resolved and its actual state has been checked. Request IDs and
retained results are future transport work, not implemented by these reporting fixes.

