import sodium from 'libsodium-wrappers';
export async function sealPassword(password, publicKey) {
  await sodium.ready;
  const key = sodium.from_base64(publicKey, sodium.base64_variants.ORIGINAL);
  if (key.length !== 32) throw new Error('Invalid GitHub encryption key');
  return sodium.to_base64(sodium.crypto_box_seal(sodium.from_string(password), key), sodium.base64_variants.ORIGINAL);
}
