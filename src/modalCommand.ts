/**
 * Parse a pasted `modal token set` command (or bare credentials) into a
 * token id / secret pair.
 *
 * Accepted shapes — flags in any order, with `=` or spaces, optional quotes,
 * and also a bare `ak-… as-…` pair:
 *
 *   modal token set --token-id ak-XXX --token-secret as-YYY
 *   modal token set --token-secret as-YYY --token-id ak-XXX
 *   modal token set --token-id="ak-XXX" --token-secret="as-YYY"
 *   ak-XXX as-YYY
 */
export type ParsedModalCommand = {
  tokenId: string;
  tokenSecret: string;
};

const TOKEN_ID = /ak-[A-Za-z0-9]+/;
const TOKEN_SECRET = /as-[A-Za-z0-9]+/;

export function parseModalCommand(input: string): ParsedModalCommand | null {
  if (!input) return null;
  const text = input.trim();
  if (!text) return null;
  const id = TOKEN_ID.exec(text);
  const secret = TOKEN_SECRET.exec(text);
  if (!id || !secret) return null;
  return { tokenId: id[0], tokenSecret: secret[0] };
}
