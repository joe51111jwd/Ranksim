/* Undo and redo over whole draft snapshots (strings). Visual edits, HTML edits, inserted blocks and head fields all
   record here, so one stack covers every mode. Typing in the same place within a second is one step. Pure. */

export class History {
  constructor({ limit = 100, window = 1000 } = {}) {
    this.limit = limit;
    this.window = window;
    this.reset('');
  }

  reset(state) {
    this.past = [];
    this.future = [];
    this.present = state;
    this.group = null;
    this.at = 0;
  }

  /* Records a new state. `group` names a typing run (for example 'type:12'); records in the same group within
     `window` ms replace the last step instead of adding one. Returns true when a step was added. */
  record(state, { group = null, now = Date.now() } = {}) {
    if (state === this.present) return false;
    const merge = group !== null && group === this.group && now - this.at <= this.window && this.past.length > 0;
    if (!merge) {
      this.past.push(this.present);
      if (this.past.length > this.limit) this.past.shift();
    }
    this.present = state;
    this.future = [];
    this.group = group;
    this.at = now;
    return !merge;
  }

  /* Ends the current typing run, so the next record is its own step. */
  seal() {
    this.group = null;
  }

  get canUndo() { return this.past.length > 0; }
  get canRedo() { return this.future.length > 0; }

  undo() {
    if (!this.past.length) return null;
    this.future.push(this.present);
    this.present = this.past.pop();
    this.group = null;
    return this.present;
  }

  redo() {
    if (!this.future.length) return null;
    this.past.push(this.present);
    this.present = this.future.pop();
    this.group = null;
    return this.present;
  }

  /* Plain data for autosave. */
  toJSON() {
    return { past: this.past.slice(-20), present: this.present, future: this.future.slice(-20) };
  }

  static from(data, opts) {
    const h = new History(opts);
    if (data && typeof data.present === 'string') {
      h.past = Array.isArray(data.past) ? data.past.filter((s) => typeof s === 'string') : [];
      h.future = Array.isArray(data.future) ? data.future.filter((s) => typeof s === 'string') : [];
      h.present = data.present;
    }
    return h;
  }
}

/* Which key press is undo or redo. Works for Cmd on a Mac and Ctrl elsewhere; Ctrl+Y is redo too. */
export function undoKey(event) {
  if (!(event.metaKey || event.ctrlKey) || event.altKey) return null;
  const key = String(event.key || '').toLowerCase();
  if (key === 'z') return event.shiftKey ? 'redo' : 'undo';
  if (key === 'y' && !event.shiftKey) return 'redo';
  return null;
}
