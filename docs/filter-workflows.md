# Document selection

The Explorer, Collections, Schemas, Analytics and Transformations share the same filters.

| State | Filter panel | Results |
| --- | --- | --- |
| Simple | Document type, correspondent, tags and date shortcuts. Custom fields on demand. | The current page and the number of matches. |
| Advanced | Existing conditions and their grouping, with precise operators. | Validation messages for unsupported combinations. |
| Empty | Remove a condition or broaden its value. | Zero matches for the current criteria. |
| Loading | Keep the current criteria editable. | Announce the pending request. |
| Error | Keep the expression available for correction. | Show the validation or connection error. |

For invoices, choose the document type, look up the correspondent by name, then
add a custom field with **Not filled in**. Use **Without a correspondent** for
uncategorized senders or **Added** with a lower date bound for recent arrivals.

Simple and advanced editing share the same FilterSet. Advanced expressions are
retained when they have no simple representation. **Not filled in** uses the
exact expression returned by the server for the selected field.

In Explorer, checked documents persist across pages and when returning through
browser history in the same session. **Prepare current page** captures that
page’s IDs. **Use all matching documents** passes the query. Transformations
still requires a reviewed preview before writing.
