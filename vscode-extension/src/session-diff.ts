// Reviewing a session's uncommitted changes from the current window: parse
// `git status --porcelain=v1 -z` into pickable entries. The NUL form is the only porcelain
// representation that preserves every legal path byte without C-quoting or an ambiguous ` -> `.
// Pure decision core —
// extension.ts runs git and opens the native diff editor.

export type ChangedFile = {
  path: string;          // repo-relative
  status: string;        // porcelain XY, trimmed (e.g. "M", "A", "??", "R")
  untracked: boolean;    // no HEAD side — diff against empty
  renamedFrom?: string;  // rename/copy's second NUL record is the old HEAD-side path
};

export function parsePorcelain(out: string): ChangedFile[] {
  const files: ChangedFile[] = [];
  const records = String(out || "").split("\0");
  for (let i = 0; i < records.length; i += 1) {
    const raw = records[i];
    if (raw.length < 4) continue;
    const xy = raw.slice(0, 2);
    const file = raw.slice(3);
    if (!file) continue;
    let renamedFrom: string | undefined;
    if (xy[0] === "R" || xy[0] === "C" || xy[1] === "R" || xy[1] === "C") {
      // In -z mode Git reverses the human short-format order: destination first, source second.
      const source = records[i + 1];
      if (source !== undefined && source !== "") {
        renamedFrom = source;
        i += 1;
      }
    }
    files.push({
      path: file,
      status: xy.trim(),
      untracked: xy === "??",
      renamedFrom,
    });
  }
  return files;
}
