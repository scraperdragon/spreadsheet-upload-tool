#!/usr/bin/env python
# ScraperWiki Limited

"""Program to extract data from an Excel spreadsheet
and store it in a tabular database.
"""

import sys
import json
import unicodecsv

# http://www.lexicon.net/sjmachin/xlrd.html
import xlrd
# https://github.com/ahupp/python-magic
import magic
# https://github.com/scraperwiki/scraperwiki_local
import scraperwiki

from collections import OrderedDict, Counter

class HeaderWidthError(Exception):
    pass


class NullHeaderError(Exception):
    pass


class ConsistencyError(Exception):
    pass


def main(argv=None):
    try:
        if argv is None:
            argv = sys.argv
        if len(argv) > 1:
            filename = argv[1]
        if len(argv) != 2:
            raise ValueError("Please supply exactly one argument")
        save(validate(extract(filename)))

    except Exception, e:
        # catch errors and wrap as JSON for frontend to display
        ret = {
            'errorType': type(e).__name__,
            'errorMessage': str(e)
        }
        return json.dumps(ret)

    else:
        # return success as JSON for frontend to display
        ret = {
            'errorType': None,
            'errorMessage': None
        }
        return json.dumps(ret)


def extract(filename, verbose=False):
    """Convert a file into a list (workbook) of lists (sheets) of lists (rows)"""

    (fileType, encoding) = detectType(filename)
    if fileType not in ['xls', 'xlsx', 'csv']:
        raise ValueError("Unknown file type <b>%s</b> (I only understand .csv, .xls and .xlsx)" % fileType)

    if fileType == 'csv':
        workbook, sheetNames = extractCSV(filename, encoding)
    else:
        workbook, sheetNames = extractExcel(filename)

    return (workbook, sheetNames)


def validate(output_from_extract):
    """perform checks on output of extract(), and if all is ok, return dicts for saving to SQLite"""

    workbook, sheetNames = output_from_extract

    for sheet in workbook:
        validateHeaders(sheet)

    # sheets will be added to this dict,
    # as lists of dicts (rather than lists of lists)
    workbookForSQL = convertToOrderedDicts(workbook, sheetNames)

    for sheetName, sheetData in workbookForSQL.iteritems():
        validateConsistency(sheetData)

    return workbookForSQL


def detectType(filename):
    """Detects the filetype of a given file.
    Possible output values are: "xls", "xlsx", "csv", or something unexpected"""
    rawFileType = magic.from_file(filename)
    if rawFileType == 'ASCII text':
        return ('csv', 'ascii')
    if 'UTF-8 Unicode' in rawFileType:
        return ('csv', 'utf-8')
    if 'ISO-8859' in rawFileType:
        return ('csv', 'latin-1')
    if rawFileType == 'Microsoft Excel 2007+':
        return ('xlsx', None)
    if 'Excel' in rawFileType:
        return ('xls', None)
    if 'Zip archive' in rawFileType and filename.endswith('.xlsx'):
        return ('xlsx', None)
    return (rawFileType, None)


def validateHeaders(rows):
    """Checks "rows" starts with a valid header row.
    rows should be a list of strings/integers/floats
    Will raise an error if:
    * the first row isn't the widest
    * the first row contains empty cells
    """
    rowLengths = [ len(row) for row in rows[1:] ]
    if len(rows[0]) < max(rowLengths):
        raise HeaderWidthError("Your header row isn't the widest in the table")

    if None in rows[0] or "" in rows[0]:
        raise NullHeaderError("Your header row contains empty cells")


def validateConsistency(dictRows, precision=0.8):
    """Checks each (non-empty) value in the list of dicts is of a consistent type.
    If a column is more than [precision]% one type, it must be entirely that type.
    """

    headers = dictRows[0].keys()

    for column in headers:
        types = [humanType(dictRow[column]) for dictRow in dictRows]
        typesInThisColumn = Counter(types)
        totalNonEmptyCells = sum(typesInThisColumn.values()) - typesInThisColumn.get('empty', 0)

        for t, frequency in typesInThisColumn.iteritems():
            # if [precision] percent of cells are of this type, they should *all* be of this type
            if precision * totalNonEmptyCells < frequency < totalNonEmptyCells:
                raise ConsistencyError("The column '%s' is not of a consistent data type" % column)


def extractExcel(filename):
    """Takes an excel file location, turns it into a
    list (workbook) of lists (sheets) of lists (rows)"""

    workbook = []
    sheetNames = []
    book = xlrd.open_workbook(filename=filename, ragged_rows=True, logfile=sys.stderr, verbosity=0)

    for sheetName in book.sheet_names():
        sheetNames.append(sheetName)
        excelSheet = book.sheet_by_name(sheetName)
        nrows = excelSheet.nrows
        sheet = []
        for rowx in range(nrows):
            row = excelSheet.row_values(rowx)
            sheet.append(row)
        workbook.append(sheet)

    return workbook, sheetNames


def extractCSV(filename, encoding):
    """Takes a csv file location, turns it into a
    list with one item which is a list (sheet) of lists (rows)"""

    workbook = []
    sheetNames = ['swdata']
    with open(filename, 'r') as f:
        sheet = []
        # we could use strict=True here too but may produce too many errors
        for row in unicodecsv.reader(f, encoding=encoding, skipinitialspace=True):
            typeConvertedRow = [ convertField(cell) for cell in row ]
            sheet.append(typeConvertedRow)
        workbook.append(sheet)

    return workbook, sheetNames


def convertToOrderedDicts(workbook, sheetNames):
    """Converts a list (workbook) of lists (sheets) of lists (rows) and
    a list of sheetNames, into a dict (workbookForSQL) of lists (sheets) of dicts (rows)"""
    workbookForSQL = OrderedDict()
    
    for sheet, sheetName in zip(workbook, sheetNames):
        sheetForSQL = []
        headers = sheet[0]
        for row in sheet[1:]:
            rowForSQL = OrderedDict( zip(headers, row) )
            sheetForSQL.append(rowForSQL)
        workbookForSQL[sheetName] = sheetForSQL

    return workbookForSQL


def save(sheets):
    tables = scraperwiki.sql.show_tables()
    for table in tables.keys():
        scraperwiki.sql.execute('drop table "%s"' % table)
        scraperwiki.sql.commit()
    for sheetName, rows in sheets.items():
        if rows:
            scraperwiki.sql.save([], rows, table_name=sheetName)


def convertField(string):
    types = [ int, float ]
    for t in types:
        try:
            return t(string.replace(',', ''))
        except ValueError:
            pass
    return string


def humanType(thing):
    t = type(thing).__name__
    types = {
        "int": "number",
        "float": "number",
        "long": "number",
        "NoneType": "empty",
        "str": "string",
        "unicode": "string"
    }
    if thing == '':
        return "empty"
    elif t in types:
        return types[t]
    else:
        return t


if __name__ == '__main__':
    print main()
    