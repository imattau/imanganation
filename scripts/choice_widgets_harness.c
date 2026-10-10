/* Harness for scripts/test-choice-widgets.sh: stubs the panel and runs the fork's choice
 * widgets (spliced in below) on a display. */

#include <gtk/gtk.h>
#include <string.h>
typedef struct { gchar *item_action_procedure; } GimpExtensionPanel;
static gboolean panel_rebuilding = FALSE;
static gchar *last_item = NULL;
static void panel_defer_run (GimpExtensionPanel *panel, const gchar *procedure, const gchar *item)
{ g_free (last_item); last_item = g_strdup (item); g_print ("RUN %s -> %s\n", procedure, item); }
static void panel_label_wrap (GtkWidget *label)
{ gtk_label_set_line_wrap (GTK_LABEL (label), TRUE); }
/*@@ CHOICE WIDGETS @@*/
int main (int argc, char **argv)
{
  GimpExtensionPanel panel = { (gchar *) "item-proc" };
  gchar **opts_one = g_strsplit ("Rooftop|Stairwell|Kitchen", "|", -1);
  gchar **opts_many = g_strsplit ("Akira|Yuki|Ren", "|", -1);
  GtkWidget *one, *many, *popover, *rows, *others, *combo_entry;
  GList *kids;

  if (!gtk_init_check (&argc, &argv)) { g_print ("NO DISPLAY\n"); return 2; }
  one = panel_choice_one_new (&panel, "pnl.location", "Rooftop", opts_one);
  many = panel_choice_many_new (&panel, "pnl.characters", "Yuki, Akira", opts_many);
  gtk_widget_show_all (one); gtk_widget_show_all (many);

  /* one: pick Kitchen (index 2) -> commits "pnl.location\tKitchen" */
  gtk_combo_box_set_active (GTK_COMBO_BOX (one), 2);
  g_assert_cmpstr (last_item, ==, "pnl.location\tKitchen");
  /* typed text + Enter */
  combo_entry = gtk_bin_get_child (GTK_BIN (one));
  gtk_entry_set_text (GTK_ENTRY (combo_entry), "Harbor pier");
  g_assert_cmpstr (last_item, ==, "pnl.location\tKitchen"); /* typing alone doesn't commit */
  g_signal_emit_by_name (combo_entry, "activate");
  g_assert_cmpstr (last_item, ==, "pnl.location\tHarbor pier");

  /* many: current "Yuki, Akira" are ticked; options are Akira Yuki Ren in the order given */
  popover = GTK_WIDGET (gtk_menu_button_get_popover (GTK_MENU_BUTTON (many)));
  rows = g_object_get_data (G_OBJECT (popover), "extension-panel-choice-rows");
  others = g_object_get_data (G_OBJECT (popover), "extension-panel-choice-others");
  kids = gtk_container_get_children (GTK_CONTAINER (rows));
  g_assert_cmpint (g_list_length (kids), ==, 3);
  g_assert_cmpstr (gtk_button_get_label (g_object_get_data (kids->data, "extension-panel-check")), ==, "Yuki");
  g_assert_cmpstr (gtk_button_get_label (g_object_get_data (g_list_nth_data (kids, 2), "extension-panel-check")), ==, "Ren");
  g_assert_true (gtk_toggle_button_get_active (GTK_TOGGLE_BUTTON (g_object_get_data (kids->data, "extension-panel-check"))));
  /* untouched close: no commit */
  g_free (last_item); last_item = NULL;
  g_signal_emit_by_name (popover, "closed");
  g_assert_null (last_item);
  /* untick Akira (2nd), tick Ren (3rd), move Ren to the top, add Mika and Sora */
  gtk_toggle_button_set_active (GTK_TOGGLE_BUTTON (g_object_get_data (g_list_nth_data (kids, 1), "extension-panel-check")), FALSE);
  gtk_toggle_button_set_active (GTK_TOGGLE_BUTTON (g_object_get_data (g_list_nth_data (kids, 2), "extension-panel-check")), TRUE);
  gtk_box_reorder_child (GTK_BOX (rows), g_list_nth_data (kids, 2), 0); /* Ren first */
  gtk_entry_set_text (GTK_ENTRY (others), " Mika ,, Sora");
  g_signal_emit_by_name (popover, "closed");
  g_assert_cmpstr (last_item, ==, "pnl.characters\tRen, Yuki, Mika, Sora");
  /* the ▲ button on the 3rd row (Akira) swaps it with the 2nd (Yuki) */
  {
    GList *rr = gtk_container_get_children (GTK_CONTAINER (rows));   /* Ren, Akira, Yuki */
    GList *buttons = gtk_container_get_children (GTK_CONTAINER (g_list_nth_data (rr, 2)));
        GtkWidget *up = NULL; GList *b;
    for (b = buttons; b; b = b->next)
      if (GTK_IS_BUTTON (b->data) && !GTK_IS_TOGGLE_BUTTON (b->data) &&
          !g_strcmp0 (gtk_button_get_label (b->data), "▲")) up = b->data;
    g_assert_nonnull (up);
    gtk_button_clicked (GTK_BUTTON (up));
    g_list_free (rr);
    rr = gtk_container_get_children (GTK_CONTAINER (rows));
    g_assert_true (g_object_get_data (g_list_nth_data (rr, 1), "extension-panel-check") ==
                   g_object_get_data (g_list_nth_data (rr, 1), "extension-panel-check"));
    g_print ("second row label: %s\n", gtk_button_get_label (g_object_get_data (g_list_nth_data (rr, 1), "extension-panel-check")));
  }
  g_print ("ALL OK\n");
  return 0;
}
