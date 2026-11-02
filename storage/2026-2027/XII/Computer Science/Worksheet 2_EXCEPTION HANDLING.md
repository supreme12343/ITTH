INDIAN SCHOOL AL WADI AL KABIR

Worksheet, 2026-27

Class: XII Comp. Sci. | Department: Computer Science | Try out during Laboratory session

Worksheet No: 2 | Topic: EXCEPTION HANDLING | Note: To be solved in the notebook

1 | Which type of error occurs when the rules of the programming language are not followed?

 a) Logical Error b) Runtime Error c) Syntax Error d) Exception

2 | What is a Python object that represents an error during program execution called?

 a) A bug b) An Exception c) A Traceback d) A Handler

3 | Which built-in exception is raised when a denominator in a division operation is zero?

 a) ValueError b) ZeroDivisionError c) IndexError d) NameError

4 | The finally block in a try...except statement is executed:
 a) Only if an exception occurs. b) Only if no exception occurs. c) Regardless of whether an exception occurred or not. d) Only if the else block is missing.

5 | Predict the output of the following code if the user enters 10 for the denominator:
try:
    numerator = 50
    denom = int(input("Enter the denominator: "))
    quotient = (numerator / denom)
    print(quotient)
    print("Division performed successfully")
except ZeroDivisionError:
    print("Denominator as ZERO.... not allowed")
print("OUTSIDE try..except block")

6 | What will be the output of the following Python code? 
try:
      x = 10 / 0 
except Exception: 
     print("Some other error!") 
except ZeroDivisionError: 
     print("Division by zero error!")

7 | Assertion (A): Every syntax error is considered an exception, but not every exception is a syntax error. 
Reason (R): Syntax errors are detected when the rules of the programming language are violated, whereas other exceptions occur during the execution of syntactically correct code.

(a) Both Assertion (A) and Reason (R) are true, and Reason (R) is the correct explanation of Assertion (A).
(b) Both Assertion (A) and Reason (R) are true, but Reason (R) is not the correct explanation of Assertion (A).
(c) Assertion (A) is true, but Reason (R) is false.
(d) Assertion (A) is false, but Reason (R) is true.

8 | Assertion (A): In a try...except...else block, the code in the else clause will only run if an exception was raised and caught by an except block. 
Reason (R): The optional else clause is intended for code that should execute only when no exceptions occur during the execution of the try block.

(a) Both Assertion (A) and Reason (R) are true, and Reason (R) is the correct explanation of Assertion (A).
(b) Both Assertion (A) and Reason (R) are true, but Reason (R) is not the correct explanation of Assertion (A).
(c) Assertion (A) is true, but Reason (R) is false.
(d) Assertion (A) is false, but Reason (R) is true.

9 | Write a program to read details of students for result preparation. Incorporate all possible exception-handling codes such as ValueError, IndexError, ZeroDivisionError etc.