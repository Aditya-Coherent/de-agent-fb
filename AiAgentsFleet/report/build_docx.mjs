// build_docx.mjs -- pure, market-agnostic layout renderer. Reads
// output/<slug>/content_plan.json (produced by build_content.py) and emits
// output/<slug>/<slug>-whitepaper.docx, in the same overall structure as
// vitamin_c_market_whitepaper_v2.docx: cover page, table of contents, 15
// numbered sections (DATA CONFIDENCE badge + body + figures/bullets/tables).
// No disclaimer page -- removed per request (analyst-facing copies).
//
// Usage: node build_docx.mjs <market-slug>

import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";
import { imageSize } from "image-size";
import {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  ImageRun, Table, TableRow, TableCell, WidthType, BorderStyle,
  ShadingType, PageBreak, TableOfContents, ExternalHyperlink,
} from "docx";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..", "..");
const slug = process.argv[2];
if (!slug) { console.error("Usage: node build_docx.mjs <market-slug>"); process.exit(2); }

const marketDir = path.join(ROOT, "AiAgentsFleet", "output", slug);
const plan = JSON.parse(fs.readFileSync(path.join(marketDir, "content_plan.json"), "utf-8"));

// ---- palette (sampled from the Vitamin C template's own chart images) ----
const NAVY = "0A2540";
const TEAL = "00B4A6";
const ORANGE = "F59E0B";
const GRAY = "64748B";
const LIGHT_GRAY = "94A3B8";
const GREEN_BG = "D1FAE5";
const GREEN_TX = "065F46";
const AMBER_BG = "FEF3C7";
const AMBER_TX = "92400E";
const RED_BG = "FEE2E2";
const RED_TX = "991B1B";

function confidenceColors(label) {
  if (label.startsWith("HIGH")) return { bg: GREEN_BG, tx: GREEN_TX };
  if (label.startsWith("LOW")) return { bg: RED_BG, tx: RED_TX };
  return { bg: AMBER_BG, tx: AMBER_TX }; // MEDIUM (and any unrecognized label, fails open to a visible warm color)
}

function imgDims(filePath, maxWidthPx = 600) {
  const buf = fs.readFileSync(filePath);
  const dim = imageSize(buf);
  const scale = Math.min(1, maxWidthPx / dim.width);
  return { buf, width: Math.round(dim.width * scale), height: Math.round(dim.height * scale) };
}

function figureBlock(fig) {
  if (!fig || !fig.path || !fs.existsSync(fig.path)) return [];
  const { buf, width, height } = imgDims(fig.path, 620);
  return [
    new Paragraph({
      alignment: AlignmentType.CENTER, spacing: { before: 200, after: 60 },
      children: [new ImageRun({ data: buf, transformation: { width, height }, type: "png" })],
    }),
    new Paragraph({
      alignment: AlignmentType.CENTER, spacing: { after: 200 },
      children: [new TextRun({ text: fig.caption || "", italics: true, size: 18, color: GRAY })],
    }),
  ];
}

function bodyParagraph(text) {
  return new Paragraph({ spacing: { after: 180 }, children: [new TextRun({ text, size: 22 })] });
}

function bulletBlock(b) {
  return [
    new Paragraph({
      spacing: { before: 120, after: 40 },
      children: [new TextRun({ text: b.heading || "", bold: true, size: 21, color: NAVY })],
    }),
    ...(b.text ? [new Paragraph({ spacing: { after: 100 }, children: [new TextRun({ text: b.text, size: 20 })] })] : []),
    ...figureBlock(b.figure),
  ];
}

function confidenceBadge(label, note) {
  if (!label || label === "N/A") return []; // e.g. the Sources & Methodology section carries no confidence badge
  const c = confidenceColors(label);
  return [
    new Paragraph({
      spacing: { before: 60, after: 40 },
      children: [
        new TextRun({ text: "DATA CONFIDENCE   -   ", bold: true, size: 18, color: GRAY }),
        new TextRun({ text: label, bold: true, size: 18, color: c.tx, shading: { type: ShadingType.CLEAR, fill: c.bg } }),
      ],
    }),
    new Paragraph({ spacing: { after: 200 }, children: [new TextRun({ text: note, italics: true, size: 18, color: GRAY })] }),
  ];
}

function makeTable(t) {
  const headerRow = new TableRow({
    tableHeader: true,
    children: t.headers.map((h) => new TableCell({
      shading: { type: ShadingType.CLEAR, fill: NAVY },
      children: [new Paragraph({ children: [new TextRun({ text: h, bold: true, color: "FFFFFF", size: 18 })] })],
    })),
  });
  const rows = t.rows.map((r, i) => new TableRow({
    children: r.map((cell) => new TableCell({
      shading: { type: ShadingType.CLEAR, fill: i % 2 === 0 ? "F8FAFC" : "FFFFFF" },
      children: [new Paragraph({ children: [new TextRun({ text: String(cell), size: 18 })] })],
    })),
  }));
  return [
    ...(t.title ? [new Paragraph({ spacing: { before: 160, after: 80 }, children: [new TextRun({ text: t.title, bold: true, size: 20, color: NAVY })] })] : []),
    new Table({ width: { size: 100, type: WidthType.PERCENTAGE }, rows: [headerRow, ...rows] }),
    new Paragraph({ text: "", spacing: { after: 200 } }),
  ];
}

function sectionBlock(s) {
  const children = [];
  children.push(new Paragraph({ children: [new PageBreak()] }));
  children.push(new Paragraph({
    spacing: { after: 20 },
    children: [new TextRun({ text: `SECTION ${s.num}`, bold: true, size: 18, color: TEAL, allCaps: true })],
  }));
  children.push(new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { after: 120 },
    children: [new TextRun({ text: s.title, bold: true, size: 32, color: NAVY })],
  }));
  children.push(...confidenceBadge(s.confidence_label, s.confidence_note));
  for (const p of s.paragraphs || []) children.push(bodyParagraph(p));
  for (const fig of s.figures || []) children.push(...figureBlock(fig));
  for (const b of s.bullets || []) children.push(...bulletBlock(b));
  for (const sh of s.subheads || []) {
    children.push(new Paragraph({
      spacing: { before: 160, after: 60 },
      children: [new TextRun({ text: sh.heading, bold: true, size: 24, color: NAVY })],
    }));
    if (sh.text) children.push(bodyParagraph(sh.text));
    for (const b of sh.bullets_inline || []) children.push(...bulletBlock(b));
    children.push(...figureBlock(sh.figure));
  }
  for (const t of s.tables || []) children.push(...makeTable(t));
  return children;
}

function coverPage() {
  const c = [];
  c.push(new Paragraph({ text: "", spacing: { before: 800 } }));
  c.push(new Paragraph({
    alignment: AlignmentType.LEFT,
    children: [new TextRun({ text: "INDUSTRY WHITEPAPER", bold: true, size: 20, color: TEAL, allCaps: true })],
  }));
  c.push(new Paragraph({
    spacing: { before: 120, after: 200 },
    children: [new TextRun({ text: `Global ${plan.root_name}`, bold: true, size: 56, color: NAVY })],
  }));
  c.push(new Paragraph({
    spacing: { after: 100 },
    children: [new TextRun({
      text: `How ${plan.article || "a"} ${plan.root_name} industry is expected to reshape itself by ${plan.forecast_year}`,
      size: 24, color: NAVY,
    })],
  }));
  c.push(new Paragraph({
    spacing: { after: 600 },
    children: [new TextRun({
      text: "An analyst's guide to market sizing, segmentation, regional balance of power, competitive landscape, "
        + "regulation, demand, and what we believe will happen next",
      size: 20, color: GRAY, italics: true,
    })],
  }));

  const metaRow = (label, value) => [
    new Paragraph({ children: [new TextRun({ text: label, bold: true, size: 16, color: TEAL, allCaps: true })] }),
    new Paragraph({ spacing: { after: 200 }, children: [new TextRun({ text: value, size: 22, color: NAVY })] }),
  ];
  c.push(...metaRow("Publication Date", new Date(plan.generated_at || Date.now()).toLocaleDateString("en-US", { month: "long", year: "numeric" })));
  c.push(...metaRow("Classification", "Indicative Estimate - AI-Assisted Desk Research"));
  c.push(...metaRow("Forecast Horizon", `${plan.base_year} through ${plan.forecast_year}`));
  c.push(new Paragraph({
    spacing: { before: 400 },
    children: [new TextRun({
      text: plan.source_note || "", size: 14, italics: true, color: LIGHT_GRAY,
    })],
  }));
  c.push(new Paragraph({ children: [new PageBreak()] }));
  return c;
}

function tocPage() {
  const c = [];
  c.push(new Paragraph({
    heading: HeadingLevel.HEADING_1,
    spacing: { after: 200 },
    children: [new TextRun({ text: "Table of Contents", bold: true, size: 32, color: NAVY })],
  }));
  for (const s of plan.sections) {
    c.push(new Paragraph({
      spacing: { after: 80 },
      children: [new TextRun({ text: `${s.num}.  ${s.title}`, size: 22, color: NAVY })],
    }));
  }
  return c;
}

const children = [
  ...coverPage(),
  ...tocPage(),
  ...plan.sections.flatMap(sectionBlock),
];

const doc = new Document({
  styles: { default: { document: { run: { font: "Calibri" } } } },
  sections: [{ properties: {}, children }],
});

const outPath = path.join(marketDir, `${slug}-whitepaper.docx`);
const buffer = await Packer.toBuffer(doc);
fs.writeFileSync(outPath, buffer);
console.log(`Wrote ${outPath}`);
