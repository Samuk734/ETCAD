# Examples

Invented drawings, generated from code: no real project, customer or product.

| Folder | Content |
|---|---|
| [`cavo_m12/`](cavo_m12) | The sheet of the demo: an M12 4-pole sensor cable to terminal block -X2 (DWG R2018 + PNG) |
| [`set_cablaggi/`](set_cablaggi) | Six A3 sheets side by side in one DWG, with the frame as a block with attributes, and the same sheets as a vector PDF whose texts are pen strokes. Previews in `anteprime/` |
| [`video/`](video) | The script that records the demo video and the screenshots of the README |

Regenerate everything with:

```bash
python examples/genera_esempi.py
```

The DWG files need the ODA File Converter (or SPAC); without it the script leaves the DXF files.

## Things to ask Claude

**Live view**

- "Open the live view and show me `examples/set_cablaggi/Set cablaggi di esempio.dwg`."
- "Draw me the sheet of an M12 8-pole cable, 3 m long, to terminal block -X7." (Claude uses the same drawing code as
  the demo, `etcad.vista.demo.scheda_cavo`, with a new specification.)
- Select an area in the page with **CHIEDI** and write "add a note here: check the length on site".

**PDF → DWG**

- "Convert `examples/set_cablaggi/Set cablaggi di esempio.pdf` to DWG in a new folder and show me the texts you are
  not sure about."
- "Compare page 4 of the PDF with the DWG you made."
- "On page 4 the cable label was read as 'Cavo PUR 4x0,34 05,2': it is 'Ø5,2', fix it and convert again."

**Editing**

- "In `Set cablaggi di esempio.dwg` set DATA to 01/03/2026 on every sheet."
- "Replace `PUR` with `PVC` in all the cable descriptions, show me the preview first."
- "Copy the cross-section of sheet 2 into sheet 5 next to the shielded cable."

**Checks**

- "Check every sheet for overlapping texts."
- "Follow the brown wire of sheet 4 from -XS10 and tell me which pins it reaches."
- "Make the bill of materials of the cable sheets, in Excel."
