ALLOWED_TAGS = [
    "math",
    "br",
    "i",
    "b",
    "u",
    "sup",
    "sub",
    "table",
    "tr",
    "td",
    "th",
    "thead",
    "tbody",
    "caption",
    "p",
    "div",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "ul",
    "ol",
    "li",
    "span",
    "img",
]

ALLOWED_ATTRIBUTES = [
    "class",
    "colspan",
    "rowspan",
    "data-bbox",
    "data-label",
]

OCR_PROMPT = f"""
OCR this image to HTML, arranged as layout blocks. Each layout block should be a div with the data-bbox attribute representing the bounding box of the block in x0 y0 x1 y1 format. Bboxes are normalized 0-1000. The data-label attribute is the label for the block.

Use the following labels:
- Caption
- Footnote
- Formula
- List-item
- Page-header
- Page-footer
- Picture
- Section-header
- Table
- Text
- Title

CRITICAL RULES:
- EVERY <div> MUST include data-label attribute.
- data-label MUST be exactly one of the allowed labels.
- NEVER omit data-label under any circumstance. If uncertain, choose the closest label.

Only use these tags {ALLOWED_TAGS}, and these attributes {ALLOWED_ATTRIBUTES}.

Guidelines:
* Inline math: Surround math with <math>...</math> tags. Math expressions should be rendered in KaTeX-compatible LaTeX. Use display for block math.
* Title and Section-header: Surround title with <h1>...</h1> tags. For Section-headers, use <h2> to <h5> tags as appropriate, ensuring the heading level reflects the correct hierarchical structure of the document.
* Tables: Use colspan and rowspan attributes to match table structure.
* Formatting: Maintain consistent formatting with the image, including spacing, indentation, subscripts/superscripts, and special characters.
* Typography: Preserve bold, italic, underline formatting using semantic HTML tags: <b> for bold text, <i> for italic text, <u> for underlined text
* Images: Include a description of any images in the alt attribute of an <img> tag. Do not fill out the src property. Describe in detail inside the div tag. Also convert charts to high fidelity data, and convert diagrams to mermaid.
* Text: join lines together properly into paragraphs using <p>...</p> tags. Use <br> tags for line breaks within paragraphs, but only when absolutely necessary to maintain meaning.
* Lists: Preserve indents and proper list markers.
* Use the simplest possible HTML structure that accurately represents the content of the block.
* Make sure the text is accurate and easy for a human to read and interpret. Reading order should be correct and natural.
""".strip()