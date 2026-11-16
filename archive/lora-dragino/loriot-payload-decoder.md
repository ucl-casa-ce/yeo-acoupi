# LORIOT payload decoder (acoupi BirdNET → LoRa)

Decodes the uplinks sent by [`acoupi_lora_bridge.py`](acoupi_lora_bridge.py).

**Payload format** (FPort 2):
```
bytes 0-3 : uint32 big-endian  unix epoch (seconds)
then, per detection, 3 bytes:
  byte +0..1 : uint16 big-endian species id
  byte +2    : uint8  confidence %  (0-100)
```
Species ids 1-30 are the curated table below (must match `SPECIES_LUT` in the sidecar);
ids ≥1000 are unmapped species (sidecar hash fallback) and render as `Unknown sp. (id N)`.

## Where it goes in LORIOT
Dashboard → your **Application** → **Output / Decoded Data** (a.k.a. payload decoder) →
paste the JavaScript below. LORIOT's "Decoded Data" uses the LoRa-Alliance codec API
`decodeUplink(input)`. The trailing wrappers (`Decoder` / `Decode`) are included in case your
LORIOT build/UI expects the older signature — harmless if unused.

```javascript
// --- acoupi BirdNET LoRa decoder ---
var SPECIES = {
  1:"Common Wood-Pigeon (Columba palumbus)",
  2:"Carrion Crow (Corvus corone)",
  3:"American Crow (Corvus brachyrhynchos)",
  4:"Eurasian Collared-Dove (Streptopelia decaocto)",
  5:"Eurasian Blackbird (Turdus merula)",
  6:"European Robin (Erithacus rubecula)",
  7:"Eurasian Blue Tit (Cyanistes caeruleus)",
  8:"Great Tit (Parus major)",
  9:"House Sparrow (Passer domesticus)",
  10:"Common Chaffinch (Fringilla coelebs)",
  11:"European Starling (Sturnus vulgaris)",
  12:"Eurasian Magpie (Pica pica)",
  13:"Eurasian Wren (Troglodytes troglodytes)",
  14:"Dunnock (Prunella modularis)",
  15:"European Goldfinch (Carduelis carduelis)",
  16:"European Greenfinch (Chloris chloris)",
  17:"Common Chiffchaff (Phylloscopus collybita)",
  18:"Eurasian Blackcap (Sylvia atricapilla)",
  19:"Song Thrush (Turdus philomelos)",
  20:"Long-tailed Tit (Aegithalos caudatus)",
  21:"Eurasian Jackdaw (Corvus monedula)",
  22:"Rook (Corvus frugilegus)",
  23:"Eurasian Jay (Garrulus glandarius)",
  24:"Common Swift (Apus apus)",
  25:"Barn Swallow (Hirundo rustica)",
  26:"European Herring Gull (Larus argentatus)",
  27:"Mallard (Anas platyrhynchos)",
  28:"Common Buzzard (Buteo buteo)",
  29:"Eurasian Kestrel (Falco tinnunculus)",
  30:"European Green Woodpecker (Picus viridis)"
};

function decodeBytes(bytes) {
  var data = { timestamp: null, detections: [] };
  if (!bytes || bytes.length < 4) {
    return { data: data, errors: ["payload shorter than 4 bytes"] };
  }
  var epoch = ((bytes[0] << 24) | (bytes[1] << 16) | (bytes[2] << 8) | bytes[3]) >>> 0;
  data.timestamp = new Date(epoch * 1000).toISOString();
  for (var i = 4; i + 3 <= bytes.length; i += 3) {
    var id = (bytes[i] << 8) | bytes[i + 1];
    var conf = bytes[i + 2];
    data.detections.push({
      species_id: id,
      species: SPECIES[id] || ("Unknown sp. (id " + id + ")"),
      confidence: conf / 100
    });
  }
  return { data: data };
}

// LoRa-Alliance / TTN / LORIOT "Decoded Data" entry point
function decodeUplink(input) {
  return decodeBytes(input.bytes);
}

// Compatibility shims for older decoder signatures (ignore if unused)
function Decoder(bytes, port) { return decodeBytes(bytes).data; }
function Decode(fPort, bytes) { return decodeBytes(bytes).data; }
```

## Example
Uplink `6a 3f b2 e4 38 e4 5c` decodes to:
```json
{ "timestamp": "2026-06-27T11:24:20.000Z",
  "detections": [ { "species_id": 14564,
                    "species": "Unknown sp. (id 14564)",
                    "confidence": 0.92 } ] }
```
(That example predates the species table, so the crow/pigeon shows as an `Unknown id`. With
the table in sync, `Columba palumbus` now encodes as id **1** → "Common Wood-Pigeon".)

## Keeping it in sync
The id↔name mapping is duplicated in two places — the sidecar `SPECIES_LUT` (name→id) and the
`SPECIES` object here (id→name). If you add a species, add it to **both**. (A more robust
long-term option is to generate both from the BirdNET label list, keyed by label index.)
