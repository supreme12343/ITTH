const forge = require('node-forge');
const fs = require('fs');

const keys = forge.pki.rsa.generateKeyPair(2048);
const cert = forge.pki.createCertificate();

cert.publicKey = keys.publicKey;
cert.serialNumber = '01';
cert.validity.notBefore = new Date();
cert.validity.notAfter = new Date();
cert.validity.notAfter.setFullYear(cert.validity.notBefore.getFullYear() + 1);
cert.setSubject([{ name: 'commonName', value: 'localhost' }]);
cert.setIssuer([{ name: 'commonName', value: 'localhost' }]);
cert.sign(keys.privateKey);

fs.writeFileSync('key.pem', forge.pki.privateKeyToPem(keys.privateKey));
fs.writeFileSync('cert.pem', forge.pki.certificateToPem(cert));

console.log('key.pem and cert.pem generated successfully.');