# JavaScript Decoder
Paste this JavaScript code straight into your The Things Network Console under Applications → Payload formatters → Uplink → Javascript. It automatically slices the incoming byte stream array apart, translates the Unix epoch integer back into an readable date string, and processes the variable detection blocks into a neat structured array.


``` javascript
function decodeUplink(input) {
  var bytes = input.bytes;
  var data = {};
  
  // 1. Unpack the 4-byte Unix Timestamp Header
  var epoch = (bytes[0] << 24) | (bytes[1] << 16) | (bytes[2] << 8) | bytes[3];
  
  // Convert back into a readable ISO date string format
  var date = new Date(epoch * 1000);
  data.timestamp = date.toISOString();
  data.detections = [];

  // Reverse Lookup Table mapping IDs back to names
  var speciesMap = {
    1001: "Carolina Wren (carwre)",
    1002: "Northern Cardinal (norcar)",
    9999: "Unknown Species"
  };

  // 2. Loop through the remaining bytes in 3-byte detection blocks
  for (var i = 4; i < bytes.length; i += 3) {
    if (i + 2 < bytes.length) {
      // Unpack 2-byte Species ID
      var speciesId = (bytes[i] << 8) | bytes[i+1];
      
      // Unpack 1-byte Confidence and scale it back to a decimal float
      var confidencePct = bytes[i+2];
      var confidence = confidencePct / 100.0;

      data.detections.push({
        species_id: speciesId,
        species_name: speciesMap[speciesId] || "Unmapped ID",
        confidence: confidence
      });
    }
  }

  return {
    data: data,
    warnings: []
  };
}
```