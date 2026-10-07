/** A counter is meaningful only inside the database history that issued it. */
export interface EditingBase {
  edit_epoch: string;
  edit_version: number;
}
