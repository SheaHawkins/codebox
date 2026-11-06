export function now(): number {
  return 42;
}

export function stamp(): number {
  return Date.now() + now();
}
