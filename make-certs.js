const selfsigned = require('selfsigned');
const fs = require('fs');

console.log("Generating secure local certificates...");
const attrs = [{ name: 'commonName', value: 'localhost' }];
const pems = selfsigned.generate(attrs, { days: 365 });

fs.writeFileSync('key.pem', pems.private);
fs.writeFileSync('cert.pem', pems.cert);
console.log("✅ key.pem and cert.pem generated successfully!");