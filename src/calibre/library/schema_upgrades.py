#!/usr/bin/env python


__license__   = 'GPL v3'
__copyright__ = '2010, Kovid Goyal <kovid@kovidgoyal.net>'
__docformat__ = 'restructuredtext en'

import os

from calibre.utils.date import DEFAULT_DATE, isoformat
from polyglot.builtins import itervalues


class SchemaUpgrade:

    def __init__(self):
        # Upgrade database
        while True:
            uv = self.user_version
            meth = getattr(self, f'upgrade_version_{uv}', None)
            if meth is None:
                break
            else:
                print(f'Upgrading database to version {uv + 1}...')
                meth()
                self.user_version = uv+1

    def upgrade_version_1(self):
        '''
        Normalize indices.
        '''
        self.conn.executescript('''\
        DROP INDEX authors_idx;
        CREATE INDEX authors_idx ON books (author_sort COLLATE NOCASE, sort COLLATE NOCASE);
        DROP INDEX series_idx;
        CREATE INDEX series_idx ON series (name COLLATE NOCASE);
        CREATE INDEX series_sort_idx ON books (series_index, id);
        ''')

    def upgrade_version_2(self):
        ''' Fix Foreign key constraints for deleting from link tables. '''
        script = '''\
        DROP TRIGGER IF EXISTS fkc_delete_books_%(ltable)s_link;
        CREATE TRIGGER fkc_delete_on_%(table)s
        BEFORE DELETE ON %(table)s
        BEGIN
            SELECT CASE
                WHEN (SELECT COUNT(id) FROM books_%(ltable)s_link WHERE %(ltable_col)s=OLD.id) > 0
                THEN RAISE(ABORT, 'Foreign key violation: %(table)s is still referenced')
            END;
        END;
        DELETE FROM %(table)s WHERE (SELECT COUNT(id) FROM books_%(ltable)s_link WHERE %(ltable_col)s=%(table)s.id) < 1;
        '''
        self.conn.executescript(script%dict(ltable='authors', table='authors', ltable_col='author'))
        self.conn.executescript(script%dict(ltable='publishers', table='publishers', ltable_col='publisher'))
        self.conn.executescript(script%dict(ltable='tags', table='tags', ltable_col='tag'))
        self.conn.executescript(script%dict(ltable='series', table='series', ltable_col='series'))

    def upgrade_version_3(self):
        ' Add path to result cache '
        self.conn.executescript('''
        DROP VIEW meta;
        CREATE VIEW meta AS
        SELECT id, title,
               (SELECT concat(name) FROM authors WHERE authors.id IN (SELECT author from books_authors_link WHERE book=books.id)) authors,
               (SELECT name FROM publishers WHERE publishers.id IN (SELECT publisher from books_publishers_link WHERE book=books.id)) publisher,
               (SELECT rating FROM ratings WHERE ratings.id IN (SELECT rating from books_ratings_link WHERE book=books.id)) rating,
               timestamp,
               (SELECT MAX(uncompressed_size) FROM data WHERE book=books.id) size,
               (SELECT concat(name) FROM tags WHERE tags.id IN (SELECT tag from books_tags_link WHERE book=books.id)) tags,
               (SELECT text FROM comments WHERE book=books.id) comments,
               (SELECT name FROM series WHERE series.id IN (SELECT series FROM books_series_link WHERE book=books.id)) series,
               series_index,
               sort,
               author_sort,
               (SELECT concat(format) FROM data WHERE data.book=books.id) formats,
               isbn,
               path
        FROM books;
        ''')

    def upgrade_version_4(self):
        'Rationalize books table'
        self.conn.executescript('''
        BEGIN TRANSACTION;
        CREATE TEMPORARY TABLE
        books_backup(id,title,sort,timestamp,series_index,author_sort,isbn,path);
        INSERT INTO books_backup SELECT id,title,sort,timestamp,series_index,author_sort,isbn,path FROM books;
        DROP TABLE books;
        CREATE TABLE books ( id      INTEGER PRIMARY KEY AUTOINCREMENT,
                             title     TEXT NOT NULL DEFAULT 'Unknown' COLLATE NOCASE,
                             sort      TEXT COLLATE NOCASE,
                             timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                             pubdate   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                             series_index REAL NOT NULL DEFAULT 1.0,
                             author_sort TEXT COLLATE NOCASE,
                             isbn TEXT DEFAULT "" COLLATE NOCASE,
                             lccn TEXT DEFAULT "" COLLATE NOCASE,
                             path TEXT NOT NULL DEFAULT "",
                             flags INTEGER NOT NULL DEFAULT 1
                        );
        INSERT INTO
            books (id,title,sort,timestamp,pubdate,series_index,author_sort,isbn,path)
            SELECT id,title,sort,timestamp,timestamp,series_index,author_sort,isbn,path FROM books_backup;
        DROP TABLE books_backup;

        DROP VIEW meta;
        CREATE VIEW meta AS
        SELECT id, title,
               (SELECT concat(name) FROM authors WHERE authors.id IN (SELECT author from books_authors_link WHERE book=books.id)) authors,
               (SELECT name FROM publishers WHERE publishers.id IN (SELECT publisher from books_publishers_link WHERE book=books.id)) publisher,
               (SELECT rating FROM ratings WHERE ratings.id IN (SELECT rating from books_ratings_link WHERE book=books.id)) rating,
               timestamp,
               (SELECT MAX(uncompressed_size) FROM data WHERE book=books.id) size,
               (SELECT concat(name) FROM tags WHERE tags.id IN (SELECT tag from books_tags_link WHERE book=books.id)) tags,
               (SELECT text FROM comments WHERE book=books.id) comments,
               (SELECT name FROM series WHERE series.id IN (SELECT series FROM books_series_link WHERE book=books.id)) series,
               series_index,
               sort,
               author_sort,
               (SELECT concat(format) FROM data WHERE data.book=books.id) formats,
               isbn,
               path,
               lccn,
               pubdate,
               flags
        FROM books;
        ''')

    def upgrade_version_5(self):
        'Update indexes/triggers for new books table'
        self.conn.executescript('''
        BEGIN TRANSACTION;
        CREATE INDEX authors_idx ON books (author_sort COLLATE NOCASE);
        CREATE INDEX books_idx ON books (sort COLLATE NOCASE);
        CREATE TRIGGER books_delete_trg
            AFTER DELETE ON books
            BEGIN
                DELETE FROM books_authors_link WHERE book=OLD.id;
                DELETE FROM books_publishers_link WHERE book=OLD.id;
                DELETE FROM books_ratings_link WHERE book=OLD.id;
                DELETE FROM books_series_link WHERE book=OLD.id;
                DELETE FROM books_tags_link WHERE book=OLD.id;
                DELETE FROM data WHERE book=OLD.id;
                DELETE FROM comments WHERE book=OLD.id;
                DELETE FROM conversion_options WHERE book=OLD.id;
        END;
        CREATE TRIGGER books_insert_trg
            AFTER INSERT ON books
            BEGIN
            UPDATE books SET sort=title_sort(NEW.title) WHERE id=NEW.id;
        END;
        CREATE TRIGGER books_update_trg
            AFTER UPDATE ON books
            BEGIN
            UPDATE books SET sort=title_sort(NEW.title) WHERE id=NEW.id;
        END;

        UPDATE books SET sort=title_sort(title) WHERE sort IS NULL;

        END TRANSACTION;
        '''
        )

    def upgrade_version_6(self):
        'Show authors in order'
        self.conn.executescript('''
        BEGIN TRANSACTION;
        DROP VIEW meta;
        CREATE VIEW meta AS
        SELECT id, title,
               (SELECT sortconcat(bal.id, name) FROM books_authors_link AS bal JOIN authors ON(author = authors.id) WHERE book = books.id) authors,
               (SELECT name FROM publishers WHERE publishers.id IN (SELECT publisher from books_publishers_link WHERE book=books.id)) publisher,
               (SELECT rating FROM ratings WHERE ratings.id IN (SELECT rating from books_ratings_link WHERE book=books.id)) rating,
               timestamp,
               (SELECT MAX(uncompressed_size) FROM data WHERE book=books.id) size,
               (SELECT concat(name) FROM tags WHERE tags.id IN (SELECT tag from books_tags_link WHERE book=books.id)) tags,
               (SELECT text FROM comments WHERE book=books.id) comments,
               (SELECT name FROM series WHERE series.id IN (SELECT series FROM books_series_link WHERE book=books.id)) series,
               series_index,
               sort,
               author_sort,
               (SELECT concat(format) FROM data WHERE data.book=books.id) formats,
               isbn,
               path,
               lccn,
               pubdate,
               flags
        FROM books;
        END TRANSACTION;
        ''')

    def upgrade_version_7(self):
        'Add uuid column'
        self.conn.executescript('''
        BEGIN TRANSACTION;
        ALTER TABLE books ADD COLUMN uuid TEXT;
        DROP TRIGGER IF EXISTS books_insert_trg;
        DROP TRIGGER IF EXISTS books_update_trg;
        UPDATE books SET uuid=uuid4();

        CREATE TRIGGER books_insert_trg AFTER INSERT ON books
        BEGIN
            UPDATE books SET sort=title_sort(NEW.title),uuid=uuid4() WHERE id=NEW.id;
        END;

        CREATE TRIGGER books_update_trg AFTER UPDATE ON books
        BEGIN
            UPDATE books SET sort=title_sort(NEW.title) WHERE id=NEW.id;
        END;

        DROP VIEW meta;
        CREATE VIEW meta AS
        SELECT id, title,
               (SELECT sortconcat(bal.id, name) FROM books_authors_link AS bal JOIN authors ON(author = authors.id) WHERE book = books.id) authors,
               (SELECT name FROM publishers WHERE publishers.id IN (SELECT publisher from books_publishers_link WHERE book=books.id)) publisher,
               (SELECT rating FROM ratings WHERE ratings.id IN (SELECT rating from books_ratings_link WHERE book=books.id)) rating,
               timestamp,
               (SELECT MAX(uncompressed_size) FROM data WHERE book=books.id) size,
               (SELECT concat(name) FROM tags WHERE tags.id IN (SELECT tag from books_tags_link WHERE book=books.id)) tags,
               (SELECT text FROM comments WHERE book=books.id) comments,
               (SELECT name FROM series WHERE series.id IN (SELECT series FROM books_series_link WHERE book=books.id)) series,
               series_index,
               sort,
               author_sort,
               (SELECT concat(format) FROM data WHERE data.book=books.id) formats,
               isbn,
               path,
               lccn,
               pubdate,
               flags,
               uuid
        FROM books;

        END TRANSACTION;
        ''')

    def upgrade_version_8(self):
        'Add Tag Browser views'
        def create_tag_browser_view(table_name, column_name):
            self.conn.executescript(f'''
                DROP VIEW IF EXISTS tag_browser_{table_name};
                CREATE VIEW tag_browser_{table_name} AS SELECT
                    id,
                    name,
                    (SELECT COUNT(id) FROM books_{table_name}_link WHERE {column_name}={table_name}.id) count
                FROM {table_name};
                ''')

        for tn in ('authors', 'tags', 'publishers', 'series'):
            cn = tn[:-1]
            if tn == 'series':
                cn = tn
            create_tag_browser_view(tn, cn)

    def upgrade_version_9(self):
        'Add custom columns'
        self.conn.executescript('''
                CREATE TABLE custom_columns (
                    id       INTEGER PRIMARY KEY AUTOINCREMENT,
                    label    TEXT NOT NULL,
                    name     TEXT NOT NULL,
                    datatype TEXT NOT NULL,
                    mark_for_delete   BOOL DEFAULT 0 NOT NULL,
                    editable BOOL DEFAULT 1 NOT NULL,
                    display  TEXT DEFAULT "{}" NOT NULL,
                    is_multiple BOOL DEFAULT 0 NOT NULL,
                    normalized BOOL NOT NULL,
                    UNIQUE(label)
                );
                CREATE INDEX custom_columns_idx ON custom_columns (label);
                CREATE INDEX IF NOT EXISTS formats_idx ON data (format);
        ''')

    def upgrade_version_10(self):
        'Add restricted Tag Browser views'
        def create_tag_browser_view(table_name, column_name, view_column_name):
            script = (f'''
                DROP VIEW IF EXISTS tag_browser_{table_name};
                CREATE VIEW tag_browser_{table_name} AS SELECT
                    id,
                    {view_column_name},
                    (SELECT COUNT(id) FROM books_{table_name}_link WHERE {column_name}={table_name}.id) count
                FROM {table_name};
                DROP VIEW IF EXISTS tag_browser_filtered_{table_name};
                CREATE VIEW tag_browser_filtered_{table_name} AS SELECT
                    id,
                    {view_column_name},
                    (SELECT COUNT(books_{table_name}_link.id) FROM books_{table_name}_link WHERE
                        {column_name}={table_name}.id AND books_list_filter(book)) count
                FROM {table_name};
                ''')
            self.conn.executescript(script)

        for field in itervalues(self.field_metadata):
            if field['is_category'] and not field['is_custom'] and 'link_column' in field:
                table = self.conn.get(
                    'SELECT name FROM sqlite_master WHERE type="table" AND name=?',
                    ('books_{}_link'.format(field['table']),), all=False)
                if table is not None:
                    create_tag_browser_view(field['table'], field['link_column'], field['column'])

    def upgrade_version_11(self):
        'Add average rating to tag browser views'
        def create_std_tag_browser_view(table_name, column_name,
                                        view_column_name, sort_column_name):
            script = (f'''
                DROP VIEW IF EXISTS tag_browser_{table_name};
                CREATE VIEW tag_browser_{table_name} AS SELECT
                    id,
                    {view_column_name},
                    (SELECT COUNT(id) FROM books_{table_name}_link WHERE {column_name}={table_name}.id) count,
                    (SELECT AVG(ratings.rating)
                     FROM books_{table_name}_link AS tl, books_ratings_link AS bl, ratings
                     WHERE tl.{column_name}={table_name}.id AND bl.book=tl.book AND
                     ratings.id = bl.rating AND ratings.rating <> 0) avg_rating,
                     {sort_column_name} AS sort
                FROM {table_name};
                DROP VIEW IF EXISTS tag_browser_filtered_{table_name};
                CREATE VIEW tag_browser_filtered_{table_name} AS SELECT
                    id,
                    {view_column_name},
                    (SELECT COUNT(books_{table_name}_link.id) FROM books_{table_name}_link WHERE
                        {column_name}={table_name}.id AND books_list_filter(book)) count,
                    (SELECT AVG(ratings.rating)
                     FROM books_{table_name}_link AS tl, books_ratings_link AS bl, ratings
                     WHERE tl.{column_name}={table_name}.id AND bl.book=tl.book AND
                     ratings.id = bl.rating AND ratings.rating <> 0 AND
                     books_list_filter(bl.book)) avg_rating,
                     {sort_column_name} AS sort
                FROM {table_name};

                ''')
            self.conn.executescript(script)

        def create_cust_tag_browser_view(table_name, link_table_name):
            script = f'''
                DROP VIEW IF EXISTS tag_browser_{table_name};
                CREATE VIEW tag_browser_{table_name} AS SELECT
                    id,
                    value,
                    (SELECT COUNT(id) FROM {link_table_name} WHERE value={table_name}.id) count,
                    (SELECT AVG(r.rating)
                     FROM {link_table_name},
                          books_ratings_link AS bl,
                          ratings AS r
                     WHERE {link_table_name}.value={table_name}.id AND bl.book={link_table_name}.book AND
                           r.id = bl.rating AND r.rating <> 0) avg_rating,
                     value AS sort
                FROM {table_name};

                DROP VIEW IF EXISTS tag_browser_filtered_{table_name};
                CREATE VIEW tag_browser_filtered_{table_name} AS SELECT
                    id,
                    value,
                    (SELECT COUNT({link_table_name}.id) FROM {link_table_name} WHERE value={table_name}.id AND
                    books_list_filter(book)) count,
                    (SELECT AVG(r.rating)
                     FROM {link_table_name},
                          books_ratings_link AS bl,
                          ratings AS r
                     WHERE {link_table_name}.value={table_name}.id AND bl.book={link_table_name}.book AND
                           r.id = bl.rating AND r.rating <> 0 AND
                           books_list_filter(bl.book)) avg_rating,
                     value AS sort
                FROM {table_name};
                '''
            self.conn.executescript(script)

        for field in itervalues(self.field_metadata):
            if field['is_category'] and not field['is_custom'] and 'link_column' in field:
                table = self.conn.get(
                    'SELECT name FROM sqlite_master WHERE type="table" AND name=?',
                    ('books_{}_link'.format(field['table']),), all=False)
                if table is not None:
                    create_std_tag_browser_view(field['table'], field['link_column'],
                                            field['column'], field['category_sort'])

        db_tables = self.conn.get('''SELECT name FROM sqlite_master
                                     WHERE type='table'
                                     ORDER BY name''')
        tables = []
        for (table,) in db_tables:
            tables.append(table)
        for table in tables:
            link_table = f'books_{table}_link'
            if table.startswith('custom_column_') and link_table in tables:
                create_cust_tag_browser_view(table, link_table)

        self.conn.execute('UPDATE authors SET sort=author_to_author_sort(name)')

    def upgrade_version_12(self):
        'DB based preference store'
        script = '''
        DROP TABLE IF EXISTS preferences;
        CREATE TABLE preferences(id INTEGER PRIMARY KEY,
                                 key TEXT NON NULL,
                                 val TEXT NON NULL,
                                 UNIQUE(key));
        '''
        self.conn.executescript(script)

    def upgrade_version_13(self):
        'Dirtied table for OPF metadata backups'
        script = '''
        DROP TABLE IF EXISTS metadata_dirtied;
        CREATE TABLE metadata_dirtied(id INTEGER PRIMARY KEY,
                             book INTEGER NOT NULL,
                             UNIQUE(book));
        INSERT INTO metadata_dirtied (book) SELECT id FROM books;
        '''
        self.conn.executescript(script)

    def upgrade_version_14(self):
        'Cache has_cover'
        self.conn.execute('ALTER TABLE books ADD COLUMN has_cover BOOL DEFAULT 0')
        data = self.conn.get('SELECT id,path FROM books', all=True)

        def has_cover(path):
            if path:
                path = os.path.join(self.library_path, path.replace('/', os.sep),
                    'cover.jpg')
                return os.path.exists(path)
            return False

        ids = [(x[0],) for x in data if has_cover(x[1])]
        self.conn.executemany('UPDATE books SET has_cover=1 WHERE id=?', ids)

    def upgrade_version_15(self):
        'Remove commas from tags'
        self.conn.execute("UPDATE OR IGNORE tags SET name=REPLACE(name, ',', ';')")
        self.conn.execute("UPDATE OR IGNORE tags SET name=REPLACE(name, ',', ';;')")
        self.conn.execute("UPDATE OR IGNORE tags SET name=REPLACE(name, ',', '')")

    def upgrade_version_16(self):
        self.conn.executescript('''
        DROP TRIGGER IF EXISTS books_update_trg;
        CREATE TRIGGER books_update_trg
            AFTER UPDATE ON books
            BEGIN
            UPDATE books SET sort=title_sort(NEW.title)
                         WHERE id=NEW.id AND OLD.title <> NEW.title;
            END;
        ''')

    def upgrade_version_17(self):
        'custom book data table (for plugins)'
        script = '''
        DROP TABLE IF EXISTS books_plugin_data;
        CREATE TABLE books_plugin_data(id INTEGER PRIMARY KEY,
                                     book INTEGER NON NULL,
                                     name TEXT NON NULL,
                                     val TEXT NON NULL,
                                     UNIQUE(book,name));
        DROP TRIGGER IF EXISTS books_delete_trg;
        CREATE TRIGGER books_delete_trg
            AFTER DELETE ON books
            BEGIN
                DELETE FROM books_authors_link WHERE book=OLD.id;
                DELETE FROM books_publishers_link WHERE book=OLD.id;
                DELETE FROM books_ratings_link WHERE book=OLD.id;
                DELETE FROM books_series_link WHERE book=OLD.id;
                DELETE FROM books_tags_link WHERE book=OLD.id;
                DELETE FROM data WHERE book=OLD.id;
                DELETE FROM comments WHERE book=OLD.id;
                DELETE FROM conversion_options WHERE book=OLD.id;
                DELETE FROM books_plugin_data WHERE book=OLD.id;
        END;
        '''
        self.conn.executescript(script)

    def upgrade_version_18(self):
        '''
        Add a library UUID.
        Add an identifiers table.
        Add a languages table.
        Add a last_modified column.
        NOTE: You cannot downgrade after this update, if you do
        any changes you make to book isbns will be lost.
        '''
        script = '''
        DROP TABLE IF EXISTS library_id;
        CREATE TABLE library_id ( id   INTEGER PRIMARY KEY,
                                  uuid TEXT NOT NULL,
                                  UNIQUE(uuid)
        );

        DROP TABLE IF EXISTS identifiers;
        CREATE TABLE identifiers  ( id     INTEGER PRIMARY KEY,
                                    book   INTEGER NON NULL,
                                    type   TEXT NON NULL DEFAULT "isbn" COLLATE NOCASE,
                                    val    TEXT NON NULL COLLATE NOCASE,
                                    UNIQUE(book, type)
        );

        DROP TABLE IF EXISTS languages;
        CREATE TABLE languages    ( id        INTEGER PRIMARY KEY,
                                    lang_code TEXT NON NULL COLLATE NOCASE,
                                    UNIQUE(lang_code)
        );

        DROP TABLE IF EXISTS books_languages_link;
        CREATE TABLE books_languages_link ( id INTEGER PRIMARY KEY,
                                            book INTEGER NOT NULL,
                                            lang_code INTEGER NOT NULL,
                                            item_order INTEGER NOT NULL DEFAULT 0,
                                            UNIQUE(book, lang_code)
        );

        DROP TRIGGER IF EXISTS fkc_delete_on_languages;
        CREATE TRIGGER fkc_delete_on_languages
        BEFORE DELETE ON languages
        BEGIN
            SELECT CASE
                WHEN (SELECT COUNT(id) FROM books_languages_link WHERE lang_code=OLD.id) > 0
                THEN RAISE(ABORT, 'Foreign key violation: language is still referenced')
            END;
        END;

        DROP TRIGGER IF EXISTS fkc_delete_on_languages_link;
        CREATE TRIGGER fkc_delete_on_languages_link
        BEFORE INSERT ON books_languages_link
        BEGIN
          SELECT CASE
              WHEN (SELECT id from books WHERE id=NEW.book) IS NULL
              THEN RAISE(ABORT, 'Foreign key violation: book not in books')
              WHEN (SELECT id from languages WHERE id=NEW.lang_code) IS NULL
              THEN RAISE(ABORT, 'Foreign key violation: lang_code not in languages')
          END;
        END;

        DROP TRIGGER IF EXISTS fkc_update_books_languages_link_a;
        CREATE TRIGGER fkc_update_books_languages_link_a
        BEFORE UPDATE OF book ON books_languages_link
        BEGIN
            SELECT CASE
                WHEN (SELECT id from books WHERE id=NEW.book) IS NULL
                THEN RAISE(ABORT, 'Foreign key violation: book not in books')
            END;
        END;
        DROP TRIGGER IF EXISTS fkc_update_books_languages_link_b;
        CREATE TRIGGER fkc_update_books_languages_link_b
        BEFORE UPDATE OF lang_code ON books_languages_link
        BEGIN
            SELECT CASE
                WHEN (SELECT id from languages WHERE id=NEW.lang_code) IS NULL
                THEN RAISE(ABORT, 'Foreign key violation: lang_code not in languages')
            END;
        END;

        DROP INDEX IF EXISTS books_languages_link_aidx;
        CREATE INDEX books_languages_link_aidx ON books_languages_link (lang_code);
        DROP INDEX IF EXISTS books_languages_link_bidx;
        CREATE INDEX books_languages_link_bidx ON books_languages_link (book);
        DROP INDEX IF EXISTS languages_idx;
        CREATE INDEX languages_idx ON languages (lang_code COLLATE NOCASE);

        DROP TRIGGER IF EXISTS books_delete_trg;
        CREATE TRIGGER books_delete_trg
            AFTER DELETE ON books
            BEGIN
                DELETE FROM books_authors_link WHERE book=OLD.id;
                DELETE FROM books_publishers_link WHERE book=OLD.id;
                DELETE FROM books_ratings_link WHERE book=OLD.id;
                DELETE FROM books_series_link WHERE book=OLD.id;
                DELETE FROM books_tags_link WHERE book=OLD.id;
                DELETE FROM books_languages_link WHERE book=OLD.id;
                DELETE FROM data WHERE book=OLD.id;
                DELETE FROM comments WHERE book=OLD.id;
                DELETE FROM conversion_options WHERE book=OLD.id;
                DELETE FROM books_plugin_data WHERE book=OLD.id;
                DELETE FROM identifiers WHERE book=OLD.id;
        END;

        INSERT INTO identifiers (book, val) SELECT id,isbn FROM books WHERE isbn;

        ALTER TABLE books ADD COLUMN last_modified TIMESTAMP NOT NULL DEFAULT "{}";

        '''.format(isoformat(DEFAULT_DATE, sep=' '))
        # Sqlite does not support non constant default values in alter
        # statements
        self.conn.executescript(script)

    def upgrade_version_19(self):
        recipes = self.conn.get('SELECT id,title,script FROM feeds')
        if recipes:
            from calibre.web.feeds.recipes import custom_recipe_filename, custom_recipes
            bdir = os.path.dirname(custom_recipes.file_path)
            for id_, title, script in recipes:
                existing = frozenset(map(int, custom_recipes))
                if id_ in existing:
                    id_ = max(existing) + 1000
                id_ = str(id_)
                fname = custom_recipe_filename(id_, title)
                custom_recipes[id_] = (title, fname)
                if isinstance(script, str):
                    script = script.encode('utf-8')
                with open(os.path.join(bdir, fname), 'wb') as f:
                    f.write(script)

    def upgrade_version_20(self):
        '''
        Add a link column to the authors table.
        '''

        script = '''
        BEGIN TRANSACTION;
        ALTER TABLE authors ADD COLUMN link TEXT NOT NULL DEFAULT "";
        '''
        self.conn.executescript(script)
