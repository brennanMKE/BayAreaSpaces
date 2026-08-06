// Hand-authored fixture for /sequoiafabrica/calendar_items?offset=20&limit=50
// &calendar=lcqebfpp6u7h&context=api
//
// Verified 2026-08-05: that endpoint answers 200 with Content-Type
// text/javascript and about 41 KB of body. Despite `context=api` it is NOT
// JSON - it is jQuery statements whose arguments are escaped HTML, which is
// why html_from_calendar_items() exists and why pagination is opt-in rather
// than the default path.
//
// Two rows here: one repeating a data-event the default page already carried
// (offset windows overlap) and one genuinely new. The repeat is collapsed and
// counted, never published twice.
$("#agenda_list tbody").append("<tr class=\"agenda__row\" data-hook=\"agenda_list_item\" data-event=\"ev-sfmao08-20261201183000\"><td class=\"date\">Tue 01 Dec<\/td><td class=\"duration\">6:30 PM – 7:30 PM PST<\/td><td class=\"title\"><button type=\"button\" class=\"agenda__title\">Member Applicant Orientation<\/button><\/td><td class=\"book\"><a href=\"\/sequoiafabrica\/e\/ev-sfmao08-20261201183000\">Book<\/a><\/td><\/tr>");
$("#agenda_list tbody").append("<tr class=\"agenda__row\" data-hook=\"agenda_list_item\" data-event=\"ev-sfcrochet4-20261124180000\"><td class=\"date\">Tue 24 Nov<\/td><td class=\"duration\">6:00 PM – 8:00 PM PST<\/td><td class=\"title\"><button type=\"button\" class=\"agenda__title\">Crochet &amp; Knitting Social<\/button><\/td><td class=\"book\"><a href=\"\/sequoiafabrica\/e\/ev-sfcrochet4-20261124180000\">Book<\/a><\/td><\/tr>");
$("#agenda_list_more").attr("data-offset", "70");
$("#flash").text("Showing 22 of 22 events");
