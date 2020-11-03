##
## CPCantalapiedra 2019

from os.path import join as pjoin
import shutil
import subprocess
from tempfile import mkdtemp
import uuid

from ...common import MMSEQS2, get_eggnog_mmseqs_db, get_call_info, ITYPE_CDS, ITYPE_PROTS, ITYPE_GENOME, ITYPE_META
from ...emapperException import EmapperException
from ...utils import colorify

from ..hmmer.hmmer_seqio import iter_fasta_seqs


class MMseqs2Searcher:

    # Command
    cpu = targetdb = temp_dir = no_file_comments = None
    start_sens = 3
    sens_steps = 3
    final_sens = 7    

    # Filters
    score_thr = evalue_thr = query_cov = subject_cov = excluded_taxa = None

    in_file = None
    itype = None

    # Results
    queries = hits = no_hits = None

    ##
    def __init__(self, args):

        self.itype = args.itype

        self.targetdb = args.mmseqs_db if args.mmseqs_db else get_eggnog_mmseqs_db()

        self.cpu = args.cpu
        self.start_sens = args.start_sens
        self.sens_steps = args.sens_steps
        self.final_sens = args.final_sens
        
        self.query_cov = args.query_cover
        self.subject_cov = args.subject_cover

        self.evalue_thr = args.mmseqs_evalue
        self.score_thr = args.mmseqs_score
        self.excluded_taxa = args.excluded_taxa if args.excluded_taxa else None
        
        self.temp_dir = args.temp_dir
        self.no_file_comments = args.no_file_comments
        
        return

    ##
    def get_hits(self):
        return self.hits

    ##
    def get_no_hits(self):
        if self.hits is not None:
            hit_queries = set([x[0] for x in self.hits])
            self.queries = set({name for name, seq in iter_fasta_seqs(self.in_file)})
            self.no_hits = set(self.queries).difference(hit_queries)
            
        return self.no_hits
    
    
    ##
    def search(self, in_file, seed_orthologs_file, hits_file = None):
        # MMseqs2Searcher does not use the "hits_file"
        # but we need this wrapper to define the search interface for Emapper
        return self._search(in_file, seed_orthologs_file)

    def _search(self, in_file, seed_orthologs_file):
        if not MMSEQS2:
            raise EmapperException("%s command not found in path" % (MMSEQS2))

        self.in_file = in_file
        
        tempdir = mkdtemp(prefix='emappertmp_mmseqs_', dir=self.temp_dir)
        try:
            querydb = pjoin(tempdir, uuid.uuid4().hex)
            print(f'Querydb {querydb}')
            resultdb = pjoin(tempdir, uuid.uuid4().hex)
            print(f'ResultDB {resultdb}')
            bestresultdb = pjoin(tempdir, uuid.uuid4().hex)
            print(f'BestResultDB {bestresultdb}')
            
            alignmentsdb, cmds = self.run_mmseqs(in_file, tempdir, querydb, self.targetdb, resultdb, bestresultdb)
            self.hits = self.parse_mmseqs(f'{alignmentsdb}.m8')
            self.output_mmseqs(cmds, self.hits, seed_orthologs_file)

        except Exception as e:
            raise e
        # finally:
        #     shutil.rmtree(tempdir)
            
        return
    
    def run_mmseqs(self, fasta_file, tempdir, querydb, targetdb, resultdb, bestresultdb):
        cmds = []
        
        cmd = self.createdb(fasta_file, querydb)
        cmds.append(cmd)

        cmd = self.search_step(querydb, targetdb, resultdb, tempdir)
        cmds.append(cmd)

        if self.itype == ITYPE_CDS or self.itype == ITYPE_PROTS:
            cmd = self.filterdb_step(resultdb, bestresultdb)
            cmds.append(cmd)
        else:
            bestresultdb = resultdb

        cmd = self.convertalis_step(querydb, targetdb, bestresultdb)
        cmds.append(cmd)

        return bestresultdb, cmds

    def createdb(self, fasta_file, querydb):
        # mmseqs createdb examples/QUERY.fasta queryDB
        cmd = (
            f'{MMSEQS2} createdb {fasta_file} {querydb}'
        )
        print(colorify('  '+cmd, 'yellow'))
        try:
            completed_process = subprocess.run(cmd, capture_output=True, check=True, shell=True)
        except subprocess.CalledProcessError as cpe:
            raise EmapperException("Error running 'mmseqs createdb': "+cpe.stderr.decode("utf-8").strip().split("\n")[-1])
        return cmd

    def search_step(self, querydb, targetdb, resultdb, tempdir):
        # mmseqs search queryDB targetDB resultDB tmp
        cmd = (
            f'{MMSEQS2} search -a true {querydb} {targetdb} {resultdb} {tempdir} '
            f'--start-sens {self.start_sens} --sens-steps {self.sens_steps} -s {self.final_sens} '
            f'--threads {self.cpu}'
        )
        
        print(colorify('  '+cmd, 'yellow'))
        try:
            completed_process = subprocess.run(cmd, capture_output=True, check=True, shell=True)
        except subprocess.CalledProcessError as cpe:
            raise EmapperException("Error running 'mmseqs search': "+cpe.stderr.decode("utf-8").strip().split("\n")[-1])
        return cmd

    ##
    def filterdb_step(self, resultdb, bestresultdb):
        # mmseqs filterdb resultDB bestResultDB --extract-lines 1
        cmd = (
            f'{MMSEQS2} filterdb {resultdb} {bestresultdb}'
        )
        if self.excluded_taxa:
            cmd += " --extract-lines 25 "
        else:
            cmd += " --extract-lines 1 "
        
        print(colorify('  '+cmd, 'yellow'))
        try:
            completed_process = subprocess.run(cmd, capture_output=True, check=True, shell=True)
        except subprocess.CalledProcessError as cpe:
            raise EmapperException("Error running 'mmseqs filterdb': "+cpe.stderr.decode("utf-8").strip().split("\n")[-1])                
        
        return cmd
    
    ##
    def convertalis_step(self, querydb, targetdb, resultdb):
        # mmseqs convertalis queryDB targetDB resultDB resultDB.m8
        cmd = (
            f'{MMSEQS2} convertalis {querydb} {targetdb} {resultdb} {resultdb}.m8'
        )
        print(colorify('  '+cmd, 'yellow'))
        try:
            completed_process = subprocess.run(cmd, capture_output=True, check=True, shell=True)
        except subprocess.CalledProcessError as cpe:
            raise EmapperException("Error running 'mmseqs convertalis': "+cpe.stderr.decode("utf-8").strip().split("\n")[-1])        
        return cmd

    
    ##
    def parse_mmseqs(self, raw_mmseqs_file):
        if self.itype == ITYPE_CDS or self.itype == ITYPE_PROTS:
            return self._parse_mmseqs(raw_mmseqs_file)
        else: #self.itype == ITYPE_GENOME or self.itype == ITYPE_META:
            return self._parse_genepred(raw_mmseqs_file)
    
    ##
    def _parse_mmseqs(self, raw_mmseqs_file):
        # From MMseqs2 documentation
        # The file is formatted as a tab-separated list with 12 columns: (1,2) identifiers for query and
        # target sequences/profiles, (3) sequence identity, (4) alignment length, (5) number of mismatches,
        # (6) number of gap openings, (7-8, 9-10) domain start and end-position in query and in target,
        # (11) E-value, and (12) bit score.
        
        hits = []

        visited = set()
        with open(raw_mmseqs_file, 'r') as raw_f:
            for line in raw_f:
                if not line.strip() or line.startswith('#'):
                    continue
                
                fields = list(map(str.strip, line.split('\t')))
            
                query = fields[0]
                hit = fields[1]
                length = int(fields[3])
                qstart = int(fields[6])
                qend = int(fields[7])
                sstart = int(fields[8])
                send = int(fields[9])
                evalue = float(fields[10])
                score = float(fields[11])

                if query in visited:
                    continue

                if evalue > self.evalue_thr or score < self.score_thr:
                    continue
                qcov = qstart - (qend - 1) / length
                if qcov < self.query_cov:
                    continue
                scov = sstart - (send - 1) / length
                if scov < self.subject_cov:
                    continue
                
                if self.excluded_taxa and hit.startswith("%s." % self.excluded_taxa):
                    continue

                visited.add(query)

                hits.append([query, hit, evalue, score])
                
        return hits

    ##
    def _parse_genepred(self, raw_mmseqs_file):
        # From MMseqs2 documentation
        # The file is formatted as a tab-separated list with 12 columns: (1,2) identifiers for query and
        # target sequences/profiles, (3) sequence identity, (4) alignment length, (5) number of mismatches,
        # (6) number of gap openings, (7-8, 9-10) domain start and end-position in query and in target,
        # (11) E-value, and (12) bit score.
        
        hits = []
        
        with open(raw_mmseqs_file, 'r') as raw_f:
            for line in raw_f:
                if not line.strip() or line.startswith('#'):
                    continue
                
                fields = list(map(str.strip, line.split('\t')))
            
                query = fields[0]
                hit = fields[1]
                length = int(fields[3])
                qstart = int(fields[6])
                qend = int(fields[7])
                sstart = int(fields[8])
                send = int(fields[9])
                evalue = float(fields[10])
                score = float(fields[11])

                if evalue > self.evalue_thr or score < self.score_thr:
                    continue
                qcov = qstart - (qend - 1) / length
                if qcov < self.query_cov:
                    continue
                scov = sstart - (send - 1) / length
                if scov < self.subject_cov:
                    continue
                
                if self.excluded_taxa and hit.startswith("%s." % self.excluded_taxa):
                    continue
                
                hit = [query, hit, evalue, score, qstart, qend, sstart, send]

                if not self._does_overlap(hit, hits):
                    hits.append(hit)
                
        return hits
    
    def _does_overlap(self, hit, hits):
        does_overlap = False
        
        hitstart = hit[4]
        hitend = hit[5]

        for o in hits:
            ostart = o[4]
            oend = o[5]
            overlap = None
            if hitend <= ostart:
                overlap = hitend - ostart
            elif hitstart >= oend:
                overlap = oend - hitstart
            else:
                overlap_start = max(hitstart, ostart)
                overlap_end = min(hitend, oend)
                overlap = overlap_end - (overlap_start - 1)

            if overlap > 0:
                does_overlap = True
                break
                
        return does_overlap
    

    ##
    def output_mmseqs(self, cmds, hits, out_file):
        if self.itype == ITYPE_CDS or self.itype == ITYPE_PROTS:
            return self._output_mmseqs(cmds, hits, out_file)
        else: #self.itype == ITYPE_GENOME or self.itype == ITYPE_META:
            return self._output_genepred(cmds, hits, out_file)
    
    ##
    def _output_mmseqs(self, cmds, hits, out_file):
        with open(out_file, 'w') as OUT:
        
            if not self.no_file_comments:
                print(get_call_info(), file=OUT)
                for cmd in cmds:
                    print('#'+cmd, file=OUT)

            for line in hits:
                print('\t'.join(map(str, line)), file=OUT)
                
        return

    ##
    def _output_genepred(self, cmds, hits, out_file):
        queries_suffixes = {}
        with open(out_file, 'w') as OUT:
        
            if not self.no_file_comments:
                print(get_call_info(), file=OUT)
                for cmd in cmds:
                    print('#'+cmd, file=OUT)

            for line in hits:
                query = line[0]
                target = line[1]
                evalue = line[2]
                score = line[3]
                if query in queries_suffixes:
                    queries_suffixes[query] += 1
                    suffix = queries_suffixes[query]
                else:
                    suffix = 0
                    queries_suffixes[query] = suffix
                    
                print('\t'.join(map(str, [f"{query}_{suffix}", target, str(evalue), str(score)])), file=OUT)
                
        return
    
## END
