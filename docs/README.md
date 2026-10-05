# Diagrams

## `FoundryIQ_Architecture.svg` / `.png`

The architecture diagram used in the main [README](../README.md) and the companion
article. Hand-drawn SVG using the Azure architecture icon set (V24). The PNG is rendered
from the SVG at 2400x1880 for embedding where SVG isn't supported.

Edit the SVG directly. To re-render the PNG:

```bash
npx playwright screenshot --viewport-size=2400,1880 \
  FoundryIQ_Architecture.svg FoundryIQ_Architecture.png
```

## `FoundryIQ_Architecture_mermaid.mmd` / `.png` / `.svg`

An earlier version of the same diagram, written in Mermaid. Kept because it regenerates
from a small text file, which makes it easier to amend as the POC changes. Not used in
the README.

```bash
npx @mermaid-js/mermaid-cli -i FoundryIQ_Architecture_mermaid.mmd \
  -o FoundryIQ_Architecture_mermaid.png -c mermaid-config.json --scale 2 -b white
```

`mermaid-config.json` holds the layout settings: font, label wrapping width and node
spacing.
