from fpdf import FPDF, XPos, YPos
import os

OUT = os.path.join(os.path.dirname(__file__), "back-arrow-logic.pdf")

class PDF(FPDF):
    def header(self):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(100, 100, 100)
        self.cell(0, 8, "PBI Implementation - Developer Reference",
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="R")
        self.ln(2)

    def footer(self):
        self.set_y(-12)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(150, 150, 150)
        self.cell(0, 8, f"Page {self.page_no()}", align="C")

    def h1(self, text):
        self.set_font("Helvetica", "B", 16)
        self.set_text_color(0, 70, 160)
        self.ln(4)
        self.cell(0, 10, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_draw_color(0, 70, 160)
        self.set_line_width(0.5)
        self.line(self.get_x(), self.get_y(), self.get_x() + 180, self.get_y())
        self.ln(4)

    def h2(self, text):
        self.set_font("Helvetica", "B", 12)
        self.set_text_color(30, 30, 30)
        self.ln(3)
        self.cell(0, 8, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(1)

    def h3(self, text):
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(60, 60, 60)
        self.ln(2)
        self.cell(0, 7, text, new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    def body(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(40, 40, 40)
        self.multi_cell(0, 6, text)
        self.ln(1)

    def code_block(self, lines):
        self.set_fill_color(245, 247, 250)
        self.set_draw_color(200, 210, 220)
        self.set_line_width(0.3)
        self.set_font("Courier", "", 8.5)
        self.set_text_color(30, 30, 30)
        line_h = 5.5
        total_h = len(lines) * line_h + 6
        x = self.get_x()
        y = self.get_y()
        self.rect(x, y, 180, total_h, style="FD")
        self.set_xy(x + 3, y + 3)
        for line in lines:
            self.cell(174, line_h, line, new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(3)

    def table(self, headers, rows, col_widths):
        self.set_font("Helvetica", "B", 9)
        self.set_fill_color(0, 70, 160)
        self.set_text_color(255, 255, 255)
        for i, h in enumerate(headers):
            self.cell(col_widths[i], 7, h, border=1, fill=True, align="C")
        self.ln()
        self.set_font("Helvetica", "", 9)
        for ri, row in enumerate(rows):
            if ri % 2 == 0:
                self.set_fill_color(245, 248, 252)
            else:
                self.set_fill_color(255, 255, 255)
            self.set_text_color(30, 30, 30)
            for i, cell in enumerate(row):
                self.cell(col_widths[i], 7, cell, border=1, fill=True, align="C")
            self.ln()
        self.ln(4)

    def bullet(self, text):
        self.set_font("Helvetica", "", 10)
        self.set_text_color(40, 40, 40)
        x = self.get_x()
        self.set_x(x + 4)
        self.cell(5, 6, "-")
        self.multi_cell(0, 6, text)
        self.set_x(x)

    def file_ref(self, label, detail):
        self.set_font("Helvetica", "B", 9)
        self.set_text_color(0, 90, 180)
        self.cell(55, 6, label)
        self.set_font("Helvetica", "", 9)
        self.set_text_color(60, 60, 60)
        self.cell(0, 6, detail, new_x=XPos.LMARGIN, new_y=YPos.NEXT)


pdf = PDF()
pdf.set_auto_page_break(auto=True, margin=15)
pdf.add_page()

# Title
pdf.h1("Back Arrow & Forward Arrow - How They Work")

# Overview
pdf.h2("Overview")
pdf.body(
    "The back (<-) and forward (->) buttons are NOT a general browser-style history. "
    "They are a one-step undo/redo specifically for the \"Clear All\" action.\n\n"
    "They are disabled by default and only become active after the user clicks Clear All."
)

# State Variables
pdf.h2("The Two State Variables")
pdf.code_block([
    "let preClearSnapshot = null;  // filter/slicer state saved just before Clear All",
    "let inClearedState   = false; // true = we are currently in the blank/cleared state",
])
pdf.body("These two variables are the entire memory of the feature.")

# Flow
pdf.h2("Step-by-Step Flow")

pdf.h3("1. User clicks \"Clear All\"")
pdf.code_block([
    "preClearSnapshot = await snapshotCurrentState();  // snapshot saved here",
    "inClearedState   = true;",
    "updateNavButtons();   // back button becomes enabled",
])
pdf.body("snapshotCurrentState() captures all report-level filters and all slicer selections across every visited page.")

pdf.h3("2. User clicks Back (<-)")
pdf.code_block([
    "if (!preClearSnapshot || !inClearedState) { return; }  // guard",
    "",
    "inClearedState = false;",
    "updateNavButtons();              // disable back, enable forward",
    "applyFilters(preClearSnapshot);  // restores pre-clear state",
])

pdf.h3("3. User clicks Forward (->)")
pdf.code_block([
    "if (!preClearSnapshot || inClearedState) { return; }  // guard",
    "",
    "inClearedState = true;",
    "updateNavButtons();                              // enable back, disable forward",
    "applyFilters({ filters: [], slicers: [] });      // re-applies blank/cleared state",
])

# Enable/Disable Table
pdf.h2("Button Enable / Disable Logic")
pdf.code_block([
    "function updateNavButtons() {",
    "    back.disabled = !preClearSnapshot || !inClearedState;",
    "    fwd.disabled  = !preClearSnapshot ||  inClearedState;",
    "}",
])
pdf.table(
    headers=["Situation", "Back", "Forward"],
    rows=[
        ["Page just loaded",          "Off", "Off"],
        ["After Clear All",           "ON",  "Off"],
        ["After clicking Back",       "Off", "ON"],
        ["After clicking Forward",    "ON",  "Off"],
        ["After applying Saved View", "Off", "Off"],
    ],
    col_widths=[90, 45, 45],
)
pdf.body("Applying a saved view resets preClearSnapshot = null and inClearedState = false, disabling both buttons.")

# applyFilters
pdf.h2("What applyFilters() Does")
pdf.body("When called with the snapshot it:")
for item in [
    "Sets report-level filters via the Power BI JS SDK",
    "Arms the slicer states so they are applied page-by-page as the user navigates",
    "Immediately applies slicers to the currently active page",
]:
    pdf.bullet(item)
pdf.ln(2)

# Key Files
pdf.h2("Key File Locations  (templates/index.html)")
refs = [
    ("Lines 388-410",   "State variables + back/forward click handlers"),
    ("Lines 391-396",   "updateNavButtons() - enable/disable logic"),
    ("Lines 1063-1117", "Clear All confirmation handler (snapshot taken here)"),
    ("Lines 521-566",   "applyFilters() - restores or clears state"),
    ("Lines 626-632",   "snapshotCurrentState() - captures filters + slicers"),
]
for label, detail in refs:
    pdf.file_ref(label, detail)
pdf.ln(4)

# Summary box
pdf.set_fill_color(240, 246, 255)
pdf.set_draw_color(0, 70, 160)
pdf.set_line_width(0.4)
x, y = pdf.get_x(), pdf.get_y()
pdf.rect(x, y, 180, 14, style="FD")
pdf.set_xy(x + 4, y + 3)
pdf.set_font("Helvetica", "B", 10)
pdf.set_text_color(0, 70, 160)
pdf.multi_cell(172, 6,
    "Summary: Back/Forward toggle between your last filter state and a blank slate "
    "-- they exist purely to undo/redo a \"Clear All\"."
)

pdf.output(OUT)
print(f"PDF written to: {OUT}")
